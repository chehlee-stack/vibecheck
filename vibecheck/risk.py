"""Per-file metrics, and the "Do Not Let AI Touch This" ranking.

Regression risk is a weighted sum of signals that each answer "how much goes wrong, and how
quietly, if an agent edits this file plausibly but wrongly?" Every signal becomes a sentence
in the file's `reasons`, so a rank is never just a number.
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Dict, List, Optional

from .contracts import Contracts
from .graph import Graph, pagerank
from .history import HistoryStats
from .project import Project

RISK_WEIGHTS = {"central": 0.24, "contracts": 0.14, "size": 0.12, "churn": 0.12, "untested": 0.12,
                "persist": 0.12, "hidden": 0.08, "fanin": 0.06}
_WRITE_CALLS = {"store_string", "store_line", "store_var", "store_buffer", "store_csv_line", "setItem", "writeFile",
                "writeFileSync", "dump", "save_png", "save_webp"}


def _percentiles(values: Dict[str, float]) -> Dict[str, float]:
    if not values:
        return {}
    ordered = sorted(values.items(), key=lambda kv: kv[1])
    n = len(ordered)
    out = {}
    for i, (k, v) in enumerate(ordered):
        out[k] = i / (n - 1) if n > 1 else 0.0
    # ties share the higher percentile
    by_val = defaultdict(list)
    for k, v in values.items():
        by_val[v].append(k)
    for v, ks in by_val.items():
        top = max(out[k] for k in ks)
        for k in ks:
            out[k] = top
    return out


def file_metrics(proj: Project, contracts: Contracts, g: Graph, history: Optional[HistoryStats], clones: dict, smells: dict) -> Dict[str, dict]:
    code = [p for p, r in proj.roles.items() if r in ("code", "generated")]
    ranked_nodes = [p for p in g.nodes if proj.roles.get(p) in ("code", "generated", "resource")]
    pr = pagerank(g, ranked_nodes)
    tests = {p for p, r in proj.roles.items() if r == "test"}
    metrics: Dict[str, dict] = {}
    table_files = Counter(t.split("::")[0] for t in contracts.tables)
    sends_in = Counter(s["table"].split("::")[0] for s in contracts.sends if s["file"] != s["table"].split("::")[0])
    sends_out = Counter(s["file"] for s in contracts.sends if s["file"] != s["table"].split("::")[0])
    persisted = set()
    for p in code:
        ff = proj.files[p]
        user_paths = [s for s, _ in ff.strings if s.startswith("user://")]
        writes = [c for c in ff.calls if c["fn"] in _WRITE_CALLS or (c["recv"] in ("ResourceSaver", "ConfigFile") and c["fn"] == "save")
                  or (c["fn"] in ("rename", "rename_absolute") and c["recv"] in ("DirAccess",))]
        if (user_paths and writes) or (ff.lang == "python" and any(c["fn"] == "open" and any(a.get("v") in ("w", "wb", "a") for a in c["args"]) for c in ff.calls)):
            persisted.add(p)
    for p in [p for p, r in proj.roles.items() if r in ("code", "generated", "test", "tool", "resource")]:
        ff = proj.files[p]
        ins = [e for e in proj.in_edges(p) if proj.roles.get(e.src) in ("code", "generated", "resource")]
        test_ins = [e for e in proj.in_edges(p) if e.src in tests]
        outs = [e for e in proj.out_edges(p) if proj.roles.get(e.dst) in ("code", "generated", "resource")]
        longest = max((f.length for f in ff.funcs), default=0)
        hidden = []
        if history and history.commits >= 5:
            for other, n, conf in history.partners(p, min_count=3, focused=2):
                key = (p, other) if p < other else (other, p)
                if conf < 0.5 or other not in proj.files or history.pair_weight.get(key, 0.0) < 0.6:
                    continue
                linked = (p, other) in proj.edges or (other, p) in proj.edges or \
                         tuple(sorted((p, other))) in contracts.string_edges
                if not linked:
                    hidden.append({"file": other, "together": n, "confidence": round(conf, 2)})
        members_used = Counter()
        for e in ins:
            for m in e.members:
                members_used[m] += 1
        metrics[p] = {
            "path": p, "role": proj.roles[p], "lang": ff.lang, "loc": ff.loc, "lines": ff.lines,
            "funcs": len(ff.funcs), "longest_func": longest,
            "longest_func_name": max(ff.funcs, key=lambda f: f.length).name if ff.funcs else None,
            "fan_in": len({e.src for e in ins}), "fan_out": len({e.dst for e in outs}),
            "tested_by": sorted({e.src for e in test_ins}),
            "pagerank": pr.get(p, 0.0),
            "churn_commits": history.changes.get(p, 0) if history else 0,
            "churn_lines": history.churn.get(p, 0) if history else 0,
            "fix_commits": history.fixes.get(p, 0) if history else 0,
            "hidden_partners": hidden[:6],
            "tables": table_files.get(p, 0), "sends_in": sends_in.get(p, 0), "sends_out": sends_out.get(p, 0),
            "invariants": len(contracts.invariants.get(p, [])),
            "dup_lines": clones["dup_lines"].get(p, 0),
            "persists": p in persisted,
            "generated_by": ff.generated_by,
            "class_name": ff.class_name,
            "autoload": next((n for n, f in proj.autoloads.items() if f == p), None),
            "export_excluded": bool(proj.export_excludes) and proj.export_excluded(p),
            "members_used": members_used.most_common(8),
            "doc": (ff.doc or "")[:220],
            "smells": smells["per_file"].get(p, {}),
        }
    return metrics


_UNTESTABLE = {"shader", "gdshader", "gdshaderinc", "glsl", "hlsl", "godot-scene", "godot-project", "godot-export", "sh"}


def _reach(proj: Project, code: List[str]) -> Dict[str, int]:
    """How many files can reach each file through static references: the most a change here can touch."""
    rev: Dict[str, List[str]] = defaultdict(list)
    cs = set(code)
    for (s, d), e in proj.edges.items():
        if s in cs and d in cs and e.kind != "ref":
            rev[d].append(s)
    out = {}
    for p in code:
        seen, stack = {p}, [p]
        while stack:
            u = stack.pop()
            for v in rev.get(u, []):
                if v not in seen:
                    seen.add(v)
                    stack.append(v)
        out[p] = len(seen) - 1
    return out


def rank(proj: Project, metrics: Dict[str, dict], history: Optional[HistoryStats]) -> List[dict]:
    code = [p for p, m in metrics.items() if m["role"] in ("code", "generated")]
    if not code:
        return []
    reach = _reach(proj, code)
    for p in code:
        metrics[p]["reach"] = reach.get(p, 0)
    pr_pct = _percentiles({p: metrics[p]["pagerank"] for p in code})
    reach_pct = _percentiles({p: reach.get(p, 0) for p in code})
    churn_pct = _percentiles({p: metrics[p]["churn_commits"] for p in code})
    has_history = bool(history and history.commits >= 5)
    persist_owners = {p for p in code if metrics[p]["persists"]}
    weights = dict(RISK_WEIGHTS)
    if not has_history:
        for k in ("churn", "hidden"):
            weights.pop(k)
    total_w = sum(weights.values())
    out = []
    for p in code:
        m = metrics[p]
        uses_persist = any(e.dst in persist_owners and e.members for e in proj.out_edges(p))
        testable = m["lang"] not in _UNTESTABLE
        s = {
            "central": 0.5 * pr_pct.get(p, 0.0) + 0.5 * reach_pct.get(p, 0.0),
            "fanin": min(1.0, m["fan_in"] / 15.0),
            "size": 0.6 * min(1.0, m["loc"] / 700.0) + 0.4 * min(1.0, m["longest_func"] / 120.0),
            "contracts": min(1.0, (m["invariants"] + m["sends_in"] + 2 * m["tables"]) / 30.0),
            "churn": churn_pct.get(p, 0.0) if has_history and m["churn_commits"] else 0.0,
            "untested": 0.0 if (m["tested_by"] or not testable) else 1.0,
            "persist": 1.0 if m["persists"] else (0.6 if uses_persist else 0.0),
            "hidden": min(1.0, len(m["hidden_partners"]) / 3.0),
        }
        if not testable:
            s["central"] *= 0.6
        score = 100.0 * sum(weights[k] * s[k] for k in weights) / total_w
        # Some damage is worse than any amount of centrality: data a real person saved.
        floor = 0.0
        if m["persists"]:
            floor = 72.0
        elif uses_persist and (m["autoload"] or m["fan_in"] >= 8):
            floor = 62.0
        score = max(score, floor)
        reasons = []
        if m["persists"]:
            reasons.append("writes persistent data: a wrong edit corrupts saved progress, and no restart undoes it")
        elif uses_persist:
            reasons.append("owns state that is saved to disk")
        if m["autoload"]:
            reasons.append("global autoload `%s`: reachable from every script" % m["autoload"])
        if m["fan_in"] >= 6:
            top = ", ".join("%s (%d)" % (name, n) for name, n in m["members_used"][:3])
            reasons.append("%d files depend on it%s" % (m["fan_in"], "; most used: " + top if top else ""))
        if reach.get(p, 0) >= 12:
            reasons.append("a change can reach %d files through references" % reach[p])
        if m["tables"] and m["sends_in"]:
            reasons.append("routes %d string key%s sent from other files" % (m["sends_in"], "" if m["sends_in"] == 1 else "s"))
        if m["invariants"] >= 5:
            reasons.append("%d string contracts pinned to it (keys, payloads, signals)" % m["invariants"])
        if m["loc"] >= 500:
            reasons.append("%d lines; longest function `%s` is %d lines" % (m["loc"], m["longest_func_name"], m["longest_func"]))
        if has_history and m["churn_commits"] >= 4:
            reasons.append("changed in %d of %d commits" % (m["churn_commits"], history.commits))
        if m["hidden_partners"]:
            hp = m["hidden_partners"][0]
            reasons.append("changes with %s (%d times) with no reference between them" % (hp["file"].rsplit("/", 1)[-1], hp["together"]))
        if not m["tested_by"] and testable:
            reasons.append("no test file references it")
        tier = "do-not-touch" if score >= 62 else "careful" if score >= 45 else "normal"
        if m["generated_by"]:
            tier = "generated"
            reasons.insert(0, "generated by %s: edit the generator, never this file" % m["generated_by"])
        out.append({"path": p, "score": round(score, 1), "tier": tier, "signals": {k: round(v, 3) for k, v in s.items()}, "reasons": reasons})
    out.sort(key=lambda r: -r["score"])
    return out


def secret_controllers(metrics: Dict[str, dict], ranking: List[dict]) -> List[dict]:
    """Small files with outsized reach: the ones that look innocent in a file tree."""
    code = [m for m in metrics.values() if m["role"] in ("code",)]
    if len(code) < 6:
        return []
    pr_pct = _percentiles({m["path"]: m["pagerank"] for m in code})
    loc_pct = _percentiles({m["path"]: m["loc"] for m in code})
    out = []
    for m in code:
        p = m["path"]
        if pr_pct[p] >= 0.8 and loc_pct[p] <= 0.5 and m["fan_in"] >= 4:
            out.append({"path": p, "loc": m["loc"], "fan_in": m["fan_in"], "reach_pct": round(pr_pct[p], 2),
                        "size_pct": round(loc_pct[p], 2)})
    return sorted(out, key=lambda d: (-d["fan_in"], d["loc"]))
