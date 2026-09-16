"""The whole pipeline for one commit: read, parse, resolve, check, measure, score."""
from __future__ import annotations

import posixpath
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from . import boundaries
from . import clones as clones_mod
from . import contracts as contracts_mod
from . import graph as graph_mod
from . import history as history_mod
from . import project as project_mod
from . import risk as risk_mod
from . import score as score_mod
from . import smells as smells_mod
from .findings import attach_source, finding, sort_findings
from .repo import Snapshot, git, is_git_repo, snapshot

VERSION = "0.1.0"


@dataclass
class Scan:
    snap: Snapshot
    proj: project_mod.Project
    history: Optional[history_mod.HistoryStats]
    commits: List[history_mod.Commit]
    contracts: contracts_mod.Contracts
    clones: dict
    smells: dict
    graph: graph_mod.Graph
    metrics: Dict[str, dict]
    ranking: List[dict]
    secret: List[dict]
    findings: List[dict]
    score: dict
    timings: Dict[str, float] = field(default_factory=dict)


def run(root: Path, ref: Optional[str] = "HEAD", commits: Optional[List[history_mod.Commit]] = None,
        with_history: bool = True, light: bool = False) -> Scan:
    t0 = time.time()
    timings = {}
    snap = snapshot(root, ref if is_git_repo(root) else None)
    proj = project_mod.load(snap)
    timings["parse"] = time.time() - t0

    t = time.time()
    hist = None
    if with_history and snap.ref:
        if commits is None:
            commits = history_mod.load(root, snap.ref)
        code_paths = {p for p, r in proj.roles.items() if r in ("code", "generated", "test", "tool", "resource", "config")}
        hist = history_mod.analyze(commits, code_paths, set(proj.docs))
    timings["history"] = time.time() - t

    t = time.time()
    con = contracts_mod.analyze(proj)
    timings["contracts"] = time.time() - t
    t = time.time()
    cl = clones_mod.detect(proj) if not light else {"pairs": [], "dup_lines": {}}
    timings["clones"] = time.time() - t
    t = time.time()
    sm = smells_mod.analyze(proj, con, hist)
    timings["smells"] = time.time() - t

    g = graph_mod.build(proj, con, hist)
    metrics = risk_mod.file_metrics(proj, con, g, hist, cl, sm)
    ranking = risk_mod.rank(proj, metrics, hist)
    secret = risk_mod.secret_controllers(metrics, ranking)

    findings = list(con.findings) + list(sm["findings"]) + boundaries.check(proj)
    findings += _size_findings(proj, metrics)
    findings += _clone_findings(proj, cl)
    hidden_pairs = _hidden_findings(proj, metrics, hist, findings)
    attach_source(findings, snap.files)
    findings = sort_findings(findings)
    sc = score_mod.compute(proj, con, hist, metrics, cl, sm, findings, hidden_pairs)
    timings["total"] = time.time() - t0
    return Scan(snap=snap, proj=proj, history=hist, commits=commits or [], contracts=con, clones=cl, smells=sm, graph=g,
                metrics=metrics, ranking=ranking, secret=secret, findings=findings, score=sc, timings=timings)


def _size_findings(proj, metrics) -> List[dict]:
    out = []
    for p, m in metrics.items():
        if m["role"] != "code":
            continue
        if m["loc"] >= 700 or m["longest_func"] >= 150:
            f = max(proj.files[p].funcs, key=lambda f: f.length) if proj.files[p].funcs else None
            sev = "medium" if (m["loc"] >= 1000 or m["longest_func"] >= 200) else "low"
            out.append(finding("size.god-file", sev, "size",
                               "%s: %d lines, %d functions, longest `%s` at %d lines" % (p.rsplit("/", 1)[-1], m["loc"], m["funcs"], m["longest_func_name"], m["longest_func"]),
                               "An agent edits a file this size through partial views. Each edit sees less of the file, and "
                               "the chance that a change collides with something out of view grows with every line.",
                               [(p, f.line if f else 1)]))
    return out


def _clone_findings(proj, cl) -> List[dict]:
    out = []
    for d in cl["pairs"]:
        if d["tokens"] < 110 or proj.roles.get(d["a"]) != "code" or proj.roles.get(d["b"]) != "code":
            continue
        same = d["a"] == d["b"]
        la, lb = d["a_lines"], d["b_lines"]
        title = ("%s repeats lines %d-%d at %d-%d" % (d["a"].rsplit("/", 1)[-1], la[0], la[1], lb[0], lb[1]) if same else
                 "%s:%d-%d is copied in %s:%d-%d" % (d["a"].rsplit("/", 1)[-1], la[0], la[1], d["b"].rsplit("/", 1)[-1], lb[0], lb[1]))
        out.append(finding("duplication.clone", "medium" if d["tokens"] >= 180 else "low", "duplication", title,
                           "%d tokens identical after renaming. A fix applied to one copy is the classic agent regression: the "
                           "other copy keeps the bug, and nothing says there are two." % d["tokens"],
                           [(d["a"], la[0]), (d["b"], lb[0])], data=d))
        if len(out) >= 12:
            break
    return out


def _hidden_findings(proj, metrics, hist, findings: List[dict]) -> int:
    if not hist or hist.commits < 5:
        return 0
    seen = set()
    for p, m in metrics.items():
        if m["role"] not in ("code", "generated"):
            continue
        for hp in m["hidden_partners"]:
            key = tuple(sorted((p, hp["file"])))
            if key in seen or proj.roles.get(hp["file"]) not in ("code", "generated"):
                continue
            seen.add(key)
            findings.append(finding("coupling.hidden", "medium" if hp["together"] >= 4 else "low", "coupling",
                                    "%s and %s change together (%d commits) with no reference between them" % (
                                        key[0].rsplit("/", 1)[-1], key[1].rsplit("/", 1)[-1], hp["together"]),
                                    "History says editing one means editing the other; the code gives an agent no way to know. "
                                    "The link is a shared idea: a format, a sequence of steps, a layout measured by hand.",
                                    [(key[0], 1), (key[1], 1)], data=hp))
    return len(seen)


# ------------------------------------------------------------------------------- serialization
def architecture(proj, g) -> dict:
    def group(p: str) -> str:
        d = posixpath.dirname(p)
        return d if d else "(root)"
    dirs = defaultdict(lambda: {"files": 0, "loc": 0, "roles": Counter()})
    for p, r in proj.roles.items():
        if r in ("code", "generated", "test", "tool", "resource"):
            d = dirs[group(p)]
            d["files"] += 1
            d["loc"] += proj.files[p].loc
            d["roles"][r] += 1
    edges = Counter()
    for (s, t), e in g.edges.items():
        if "static" in e["kinds"] or "contract" in e["kinds"]:
            a, b = group(s), group(t)
            if a != b:
                edges[(a, b)] += 1
    return {"dirs": [{"dir": k, "files": v["files"], "loc": v["loc"], "role": v["roles"].most_common(1)[0][0]} for k, v in sorted(dirs.items())],
            "edges": [{"s": a, "t": b, "n": n} for (a, b), n in edges.most_common()]}


def search_index(scan: Scan) -> dict:
    """Terms per file for the report's in-page blast radius (the same fields blast.py indexes)."""
    from .blast import file_terms
    return {p: file_terms(scan, p) for p in scan.graph.nodes if scan.proj.roles.get(p) in ("code", "generated", "test", "resource")}


def to_json(scan: Scan, root: Path, extras: Optional[dict] = None) -> dict:
    proj, hist = scan.proj, scan.history
    code = [p for p, r in proj.roles.items() if r == "code"]
    branch = ""
    subject = ""
    commit_ts = 0
    if scan.snap.ref:
        try:
            names = git(root, "branch", "--contains", scan.snap.ref, "--format=%(refname:short)", check=False).decode().split()
            branch = names[0] if names else ""
            subject, ts = git(root, "log", "-1", "--format=%s%x1f%ct", scan.snap.ref).decode().strip().split("\x1f")
            commit_ts = int(ts)
        except Exception:
            pass
    risk_by = {r["path"]: r for r in scan.ranking}
    nodes = []
    for p in scan.graph.nodes:
        m = scan.metrics.get(p, {})
        r = risk_by.get(p)
        nodes.append({"id": p, "role": proj.roles.get(p), "loc": m.get("loc", 0), "risk": r["score"] if r else None,
                      "tier": r["tier"] if r else None, "dir": posixpath.dirname(p) or "(root)", "fan_in": m.get("fan_in", 0),
                      "autoload": m.get("autoload"), "class_name": m.get("class_name")})
    edges = []
    for (s, t), e in scan.graph.edges.items():
        pe = proj.edges.get((s, t))
        edges.append({"s": s, "t": t, "w": round(e["w"], 4), "kinds": {k: round(v, 4) for k, v in e["kinds"].items()}, "why": e["why"][:3],
                      "members": sorted(pe.members) if pe else [], "skind": pe.kind if pe else None})
    from . import blast as blast_mod
    constraint_sents = []
    for path, df in proj.docs.items():
        if df.agent_weight < 1 or df.is_prompt_file:
            continue
        for s, ln, heading in df.sentences:
            if len(s) < 12 or len(s) > 420:
                continue
            constraint_sents.append({"doc": path, "line": ln, "text": s, "heading": heading, "weight": df.agent_weight})
    table_rules = blast_mod._table_rules(scan, set(proj.classes) | {p.rsplit("/", 1)[-1] for p in proj.files})
    symbols = {}
    for p in scan.graph.nodes:
        ff = proj.files.get(p)
        if ff and proj.roles.get(p) in ("code", "generated"):
            symbols[p] = [[s["name"], sorted({blast_mod.stem(w) for w in blast_mod.words(s["name"] + " " + s.get("doc", ""))})[:40], s["line"]]
                          for s in ff.symbols]
    presets = []
    for f in proj.files.values():
        if f.lang == "godot-export":
            presets = [{"name": pr["name"], "internet": pr.get("internet")} for pr in f.extra.get("presets", [])]
    out = {
        "tool": {"name": "VibeCheck", "version": VERSION},
        "repo": {"name": root.resolve().name, "ref": scan.snap.ref, "short": scan.snap.short, "branch": branch,
                 "subject": subject, "commit_ts": commit_ts, "scanned_at": int(time.time()), "godot": proj.godot_root is not None},
        "stats": {
            "files": len(scan.snap.all_paths), "text_files": len(scan.snap.files), "code_files": len(code),
            "loc": sum(scan.metrics[p]["loc"] for p in code), "languages": proj.languages,
            "roles": dict(Counter(proj.roles.values())), "docs": len(proj.docs),
            "tests": sorted(p for p, r in proj.roles.items() if r == "test"),
            "commits": hist.commits if hist else 0, "ai_commits": hist.ai_commits if hist else 0,
            "ai_agents": dict(hist.ai_agents) if hist else {}, "docs_only_commits": hist.docs_only_commits if hist else 0,
            "first_ts": hist.first_ts if hist else 0, "last_ts": hist.last_ts if hist else 0,
            "median_commit_files": hist.median_code_files if hist else 0, "median_commit_lines": hist.median_code_lines if hist else 0,
            "edges": len(proj.edges), "tables": len(scan.contracts.tables), "sends": len(scan.contracts.sends),
            "signals": len(scan.contracts.signals), "timings": {k: round(v, 2) for k, v in scan.timings.items()},
        },
        "score": scan.score,
        "findings": scan.findings,
        "risk": scan.ranking,
        "secret_controllers": scan.secret,
        "metrics": scan.metrics,
        "graph": {"nodes": nodes, "edges": edges},
        "architecture": architecture(proj, scan.graph),
        "contracts": {
            "tables": [{"id": t.id, "file": t.file, "name": t.name, "line": t.line, "keys": len(t.keys), "access": t.access,
                        "handlers": len(t.handlers), "open": t.open,
                        "sends": sum(1 for s in scan.contracts.sends if s["table"] == t.id),
                        "senders": sorted({s["file"] for s in scan.contracts.sends if s["table"] == t.id})}
                       for t in scan.contracts.tables.values() if any(s["table"] == t.id for s in scan.contracts.sends)],
            "signals": scan.contracts.signals,
            "invariants": {p: v[:40] for p, v in scan.contracts.invariants.items()},
        },
        "clones": scan.clones["pairs"][:40],
        "prompts": scan.smells["prompts"],
        "agent_docs": scan.smells["agent_docs"],
        "history": {
            "commits": [{"sha": c.sha[:7], "ts": c.ts, "subject": c.subject, "ai": c.ai, "fix": c.fixlike,
                         "files": len([p for p in c.files if p in proj.files])} for c in scan.commits][-400:],
        },
        "docs_index": constraint_sents,
        "table_rules": [{"doc": r["doc"], "line": r["line"], "text": r["text"], "weight": r["weight"]} for r in table_rules],
        "commands": blast_mod._commands(scan)[:6],
        "index": search_index(scan),
        "symbols": symbols,
        "godot": {"export_excludes": proj.export_excludes, "presets": presets, "autoloads": proj.autoloads},
        "blast_config": {"stop": sorted(blast_mod.STOP), "concepts": blast_mod.CONCEPTS,
                         "intents": [[n, p] for n, p in blast_mod.INTENTS], "neg_words": sorted(blast_mod._NEG_WORDS),
                         "generic": sorted(blast_mod._GENERIC_TERMS), "constraint_re": blast_mod.CONSTRAINT_RE.pattern},
    }
    if extras:
        out.update(extras)
    return out
