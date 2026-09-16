"""vibecheck — an auditor for codebases built with AI agents.

  vibecheck scan REPO [--ref REF] [--out DIR] [--ask "request" ...] [--timeline] [--backtest] [--review]
  vibecheck blast REPO "request" [--ref REF] [--seed PATH ...] [--json]
  vibecheck check REPO [--ref REF] [--fail-on high|medium|low]
  vibecheck timeline REPO [--ref REF] [--out FILE]
  vibecheck backtest REPO [--ref REF] [--out FILE]

REPO is a local path or a GitHub URL (owner/name works too).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

from . import blast as blast_mod
from . import repo as repo_mod
from . import scan as scan_mod
from .findings import SEVERITY_RANK

DEFAULT_ASKS = []


def _root(target: str, workdir: str) -> Path:
    if repo_mod.is_url(target):
        wd = Path(workdir).expanduser()
        print("cloning %s into %s ..." % (target, wd), file=sys.stderr)
        return repo_mod.clone(target, wd)
    p = Path(target).expanduser()
    if not p.exists():
        sys.exit("vibecheck: no such path: %s" % target)
    return p


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "request"


def _progress(label: str):
    def cb(i, *rest):
        total = rest[0] if rest and isinstance(rest[0], int) else None
        commit = rest[-1] if rest else None
        subject = getattr(commit, "subject", "")[:60]
        print("  %s %s%s  %s" % (label, i, "/%d" % total if total else "", subject), file=sys.stderr)
    return cb


def cmd_scan(a) -> int:
    root = _root(a.repo, a.workdir)
    out = Path(a.out)
    t = time.time()
    s = scan_mod.run(root, a.ref)
    extras = {"blasts": [], "timeline": None, "backtest": None, "review": None}
    for req in a.ask or DEFAULT_ASKS:
        b = blast_mod.run(s, req)
        extras["blasts"].append(b)
        pack = out / "handoff" / ("%s.md" % _slug(req))
        pack.parent.mkdir(parents=True, exist_ok=True)
        pack.write_text(b["pack"], encoding="utf-8")
    if a.timeline:
        from . import timeline
        print("timeline: scanning every commit ...", file=sys.stderr)
        extras["timeline"] = timeline.run(root, a.ref, progress=_progress("commit"))
    if a.backtest:
        from . import backtest
        print("backtest: replaying history ...", file=sys.stderr)
        extras["backtest"] = backtest.run(root, a.ref, progress=_progress("sample"))
    result = scan_mod.to_json(s, root, extras)
    from . import llm
    pack_text = llm.context_pack(result)
    out.mkdir(parents=True, exist_ok=True)
    (out / "review-prompt.md").write_text(llm.REVIEW_SYSTEM + "\n\n---\n\n" + pack_text, encoding="utf-8")
    if a.review:
        try:
            result["review"] = llm.review(pack_text)
        except RuntimeError as e:
            print("review skipped: %s" % e, file=sys.stderr)
    (out / "vibecheck.json").write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
    from . import report
    path = report.render(result, out / "report.html")
    sc = result["score"]
    print("\nVibeCheck  %s @ %s   (%.1fs)" % (result["repo"]["name"], result["repo"]["short"], time.time() - t))
    print("Vibe Debt Score  %.0f / 100  —  %s" % (sc["total"], sc["tier_label"]))
    for c in sc["categories"].values():
        print("  %-20s %5.1f" % (c["label"], c["score"]))
    sev = {}
    for f in result["findings"]:
        sev[f["severity"]] = sev.get(f["severity"], 0) + 1
    print("Findings: %s" % ", ".join("%d %s" % (sev[k], k) for k in ("high", "medium", "low", "info") if k in sev))
    print("Do not let AI touch: %s" % ", ".join(r["path"] for r in result["risk"] if r["tier"] in ("do-not-touch",))[:300])
    print("\nreport   %s\ndata     %s\nreview   %s" % (path, out / "vibecheck.json", out / "review-prompt.md"))
    for b in extras["blasts"]:
        print("handoff  %s" % (out / "handoff" / ("%s.md" % _slug(b["request"]))))
    return 0


def cmd_blast(a) -> int:
    root = _root(a.repo, a.workdir)
    s = scan_mod.run(root, a.ref)
    b = blast_mod.run(s, a.request, seeds=a.seed or None)
    if a.json:
        print(json.dumps({k: v for k, v in b.items() if k != "pack"}, indent=1))
    else:
        print(b["pack"])
    return 0


def cmd_check(a) -> int:
    root = _root(a.repo, a.workdir)
    s = scan_mod.run(root, a.ref, light=True)
    bar = SEVERITY_RANK[a.fail_on]
    failing = [f for f in s.findings if SEVERITY_RANK.get(f["severity"], 0) >= bar and f["category"] == "agent-risk"]
    for f in failing:
        ev = f["evidence"][0] if f["evidence"] else {}
        print("%s:%s: %s: %s" % (ev.get("file", "?"), ev.get("line", 0), f["severity"], f["title"]))
    print("vibecheck check: %d finding%s at %s or above" % (len(failing), "" if len(failing) == 1 else "s", a.fail_on), file=sys.stderr)
    return 1 if failing else 0


def cmd_timeline(a) -> int:
    from . import timeline
    root = _root(a.repo, a.workdir)
    tl = timeline.run(root, a.ref, progress=_progress("commit"))
    Path(a.out).write_text(json.dumps(tl, indent=1), encoding="utf-8")
    for x in tl:
        print("%s  %5.1f  %s" % (x["sha"], x["score"], x["subject"][:70]))
    return 0


def cmd_backtest(a) -> int:
    from . import backtest
    root = _root(a.repo, a.workdir)
    res = backtest.run(root, a.ref, progress=_progress("sample"))
    Path(a.out).write_text(json.dumps(res, indent=1), encoding="utf-8")
    for mode, methods in res["summary"].items():
        print("\n%s (%d commits)" % (mode, res["samples"]))
        for m, v in sorted(methods.items(), key=lambda kv: -kv[1]["recall@5"]):
            print("  %-16s recall@5 %.2f  recall@10 %.2f  hit@5 %.2f  mrr %.2f" % (m, v["recall@5"], v["recall@10"], v["hit@5"], v["mrr"]))
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="vibecheck", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--workdir", default="~/.cache/vibecheck/repos", help="where GitHub repositories are cloned")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("scan", help="full audit and HTML report")
    sp.add_argument("repo")
    sp.add_argument("--ref", default="HEAD")
    sp.add_argument("--out", default="vibecheck-report")
    sp.add_argument("--ask", action="append", help="a change request to precompute a blast radius and handoff pack for")
    sp.add_argument("--timeline", action="store_true", help="score every commit (slow on long histories)")
    sp.add_argument("--backtest", action="store_true", help="measure blast-radius predictions against history")
    sp.add_argument("--review", action="store_true", help="ask Claude for an architectural review (needs `pip install anthropic`)")
    sp.set_defaults(fn=cmd_scan)

    bp = sub.add_parser("blast", help="blast radius and agent handoff pack for one change request")
    bp.add_argument("repo")
    bp.add_argument("request")
    bp.add_argument("--ref", default="HEAD")
    bp.add_argument("--seed", action="append", help="start from this file instead of searching")
    bp.add_argument("--json", action="store_true")
    bp.set_defaults(fn=cmd_blast)

    cp = sub.add_parser("check", help="CI gate: exit 1 on agent-risk findings at or above a severity")
    cp.add_argument("repo")
    cp.add_argument("--ref", default="HEAD")
    cp.add_argument("--fail-on", default="high", choices=["high", "medium", "low"])
    cp.set_defaults(fn=cmd_check)

    tp = sub.add_parser("timeline", help="the score at every commit")
    tp.add_argument("repo")
    tp.add_argument("--ref", default="HEAD")
    tp.add_argument("--out", default="vibecheck-timeline.json")
    tp.set_defaults(fn=cmd_timeline)

    kp = sub.add_parser("backtest", help="how well the blast radius predicted real commits")
    kp.add_argument("repo")
    kp.add_argument("--ref", default="HEAD")
    kp.add_argument("--out", default="vibecheck-backtest.json")
    kp.set_defaults(fn=cmd_backtest)

    a = p.parse_args(argv)
    return a.fn(a)
