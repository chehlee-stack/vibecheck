"""The Vibe Debt Score: 0 is a repo you can hand to an agent blind, 100 is one you cannot.

Six categories, each the mean of named components, each component a measured value mapped
through a ramp whose two ends are written below. The ends are hand-set (scoring v0): they
encode judgment, not a fit to a corpus of repositories, and the report says so.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Dict, List, Optional

from .contracts import Contracts
from .findings import SEVERITY_RANK
from .history import HistoryStats
from .project import Project

WEIGHTS = {"coupling": 0.20, "duplication": 0.10, "size": 0.15, "agent_risk": 0.25, "test_protection": 0.15, "prompt_archaeology": 0.15}
LABELS = {
    "coupling": "Coupling", "duplication": "Duplication", "size": "God objects", "agent_risk": "Agent-edit risk",
    "test_protection": "Test protection", "prompt_archaeology": "Prompt archaeology",
}
TIERS = [(25, "handover", "Hand it over"), (45, "pack", "Hand over with a pack"),
         (65, "supervised", "Supervised edits only"), (101, "stabilize", "Stabilize first")]
VERSION = "v0 (hand-set thresholds)"


def ramp(x: float, lo: float, hi: float) -> float:
    if hi == lo:
        return 100.0 if x >= hi else 0.0
    t = (x - lo) / (hi - lo)
    return round(100.0 * max(0.0, min(1.0, t)), 1)


def _sccs(nodes: List[str], edges: Dict[str, List[str]]) -> List[List[str]]:
    index, low, stack, on, out = {}, {}, [], set(), []
    counter = [0]

    def strong(v: str) -> None:
        work = [(v, iter(edges.get(v, [])))]
        index[v] = low[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        on.add(v)
        while work:
            node, it = work[-1]
            advanced = False
            for w in it:
                if w not in index:
                    index[w] = low[w] = counter[0]
                    counter[0] += 1
                    stack.append(w)
                    on.add(w)
                    work.append((w, iter(edges.get(w, []))))
                    advanced = True
                    break
                elif w in on:
                    low[node] = min(low[node], index[w])
            if not advanced:
                work.pop()
                if work:
                    low[work[-1][0]] = min(low[work[-1][0]], low[node])
                if low[node] == index[node]:
                    comp = []
                    while True:
                        w = stack.pop()
                        on.discard(w)
                        comp.append(w)
                        if w == node:
                            break
                    out.append(comp)

    for n in nodes:
        if n not in index:
            strong(n)
    return out


def compute(proj: Project, contracts: Contracts, history: Optional[HistoryStats], metrics: Dict[str, dict],
            clones: dict, smells: dict, findings: List[dict], hidden_pairs: int) -> dict:
    code = [p for p, r in proj.roles.items() if r == "code"]
    code_loc = max(1, sum(metrics[p]["loc"] for p in code if p in metrics))
    kloc = code_loc / 1000.0
    cats: Dict[str, dict] = {}

    def cat(name: str, comps: List[dict], credit: float = 0.0, credit_note: str = "") -> None:
        live = [c for c in comps if c.get("score") is not None]
        s = sum(c["score"] for c in live) / len(live) if live else 0.0
        s = max(0.0, s - credit)
        cats[name] = {"label": LABELS[name], "score": round(s, 1), "components": comps, "credit": credit, "credit_note": credit_note}

    # coupling
    code_set = set(code)
    static_in = Counter()
    adj: Dict[str, List[str]] = {}
    for (s, d), e in proj.edges.items():
        if s in code_set and d in code_set:
            static_in[d] += 1
            adj.setdefault(s, []).append(d)
    total_in = sum(static_in.values()) or 1
    top3 = sum(n for _, n in static_in.most_common(3)) / total_in
    avg_out = sum(len(set(v)) for v in adj.values()) / max(1, len(code))
    cyc = [c for c in _sccs(code, adj) if len(c) > 1]
    in_cycles = sum(len(c) for c in cyc) / max(1, len(code))
    implicit = len({(s["file"], s["table"]) for s in contracts.sends}) + sum(1 for e in contracts.string_edges.values() if e["weight"] >= 1.0)
    implicit_ratio = implicit / max(1, sum(1 for (s, d) in proj.edges if s in code_set and d in code_set))
    comps = [
        {"name": "Hub concentration", "value": "%.0f%% of references land on 3 files" % (100 * top3), "score": ramp(top3, 0.15, 0.5), "range": [0.15, 0.5]},
        {"name": "Average fan-out", "value": "%.1f files per file" % avg_out, "score": ramp(avg_out, 3, 10), "range": [3, 10]},
        {"name": "Dependency cycles", "value": "%.0f%% of files sit in a cycle" % (100 * in_cycles), "score": ramp(in_cycles, 0.05, 0.35), "range": [0.05, 0.35]},
        {"name": "Implicit coupling", "value": "%.2f string/contract links per static link" % implicit_ratio, "score": ramp(implicit_ratio, 0.3, 1.2), "range": [0.3, 1.2]},
    ]
    if history and history.commits >= 5:
        per10 = hidden_pairs / max(1, len(code)) * 10
        comps.append({"name": "Hidden co-change", "value": "%d pairs change together with no reference" % hidden_pairs, "score": ramp(per10, 0.5, 5), "range": [0.5, 5]})
    else:
        comps.append({"name": "Hidden co-change", "value": "not measured: too little history", "score": None})
    cat("coupling", comps)

    # duplication
    dup = sum(v for p, v in clones["dup_lines"].items() if proj.roles.get(p) == "code")
    dup_pct = 100.0 * dup / code_loc
    scattered = smells["counts"].get("scattered_constants", 0)
    cat("duplication", [
        {"name": "Cloned code", "value": "%.1f%% of code lines appear twice or more" % dup_pct, "score": ramp(dup_pct, 3, 15), "range": [3, 15]},
        {"name": "Copied constants", "value": "%d literals repeated across 3+ files" % scattered, "score": ramp(scattered / kloc, 0, 2), "range": [0, 2]},
    ])

    # size
    big = sum(metrics[p]["loc"] for p in code if metrics[p]["loc"] >= 600)
    long_funcs = sum(1 for p in code for f in proj.files[p].funcs if f.length >= 80)
    biggest = max((metrics[p]["loc"] for p in code), default=0)
    cat("size", [
        {"name": "Code in large files", "value": "%.0f%% of lines in files over 600 lines" % (100 * big / code_loc), "score": ramp(big / code_loc, 0.1, 0.6), "range": [0.1, 0.6]},
        {"name": "Long functions", "value": "%d functions of 80+ lines" % long_funcs, "score": ramp(long_funcs / kloc, 0.2, 2), "range": [0.2, 2]},
        {"name": "Largest file", "value": "%d lines" % biggest, "score": ramp(biggest, 600, 2500), "range": [600, 2500]},
    ])

    # agent-edit risk
    weighted = sum({"high": 10, "medium": 4, "low": 1}.get(f["severity"], 0) for f in findings
                   if f["category"] == "agent-risk" and not f["id"].startswith("smell.warnings"))
    stringly = (len(contracts.sends) + sum(1 for e in contracts.string_edges.values() if e["weight"] >= 1.0)) / kloc
    silenced = smells["counts"].get("silenced_warnings", 0)
    ignores = smells["counts"].get("inline_ignores", 0) / kloc
    has_agent_docs = smells["counts"].get("agent_docs", 0) > 0
    arch_tests = [p for p, r in proj.roles.items() if r == "test" and re.search(r"architect|boundar|contract|dependency|layer|lint", p, re.I)]
    credit = (10.0 if has_agent_docs else 0.0) + (10.0 if arch_tests else 0.0)
    notes = []
    if has_agent_docs:
        notes.append("agent instructions present")
    if arch_tests:
        notes.append("architecture enforced by %s" % ", ".join(p.rsplit("/", 1)[-1] for p in arch_tests))
    cat("agent_risk", [
        {"name": "Broken or dangling contracts", "value": "%d weighted findings" % weighted, "score": ramp(weighted / kloc, 0, 4), "range": [0, 4]},
        {"name": "Stringly-typed density", "value": "%.0f string-keyed links per 1k lines" % stringly, "score": ramp(stringly, 4, 30), "range": [4, 30]},
        {"name": "Silenced analysis", "value": "%d warnings off project-wide, %d inline ignores" % (silenced, smells["counts"].get("inline_ignores", 0)),
         "score": max(ramp(silenced, 1, 10), ramp(ignores, 0, 3)), "range": [1, 10]},
    ], credit=credit, credit_note="; ".join(notes))

    # test protection
    tests = [p for p, r in proj.roles.items() if r == "test"]
    if not tests:
        cat("test_protection", [{"name": "Tests", "value": "no test files found", "score": 100.0}])
    else:
        untested = [p for p in code if not metrics[p]["tested_by"]]
        test_loc = sum(proj.files[p].loc for p in tests)
        cat("test_protection", [
            {"name": "Files no test references", "value": "%d of %d" % (len(untested), len(code)), "score": ramp(len(untested) / max(1, len(code)), 0.3, 0.9), "range": [0.3, 0.9]},
            {"name": "Test volume", "value": "%.2f test lines per code line" % (test_loc / code_loc), "score": 100.0 - ramp(test_loc / code_loc, 0.05, 0.4), "range": [0.4, 0.05]},
        ])

    # prompt archaeology
    c = smells["counts"]
    leftovers = (5 * c.get("elision", 0) + 3 * c.get("chat", 0) + c.get("stub", 0) + 0.2 * c.get("todo", 0)) / kloc
    deadish = sum(1 for f in findings if f["id"] in ("contract.unsent-key", "contract.dead-branch", "godot.signal-never-emitted", "contract.dropped-key"))
    cat("prompt_archaeology", [
        {"name": "Stale references in agent-facing docs", "value": "%d" % c.get("stale_doc_refs_agent", 0), "score": ramp(c.get("stale_doc_refs_agent", 0), 0, 20), "range": [0, 20]},
        {"name": "Dead prompts still in the repo", "value": "%d" % c.get("dead_prompts", 0), "score": ramp(c.get("dead_prompts", 0), 0, 6), "range": [0, 6]},
        {"name": "AI leftovers in code", "value": "%d elision, %d chat-voice, %d stub, %d TODO" % (c.get("elision", 0), c.get("chat", 0), c.get("stub", 0), c.get("todo", 0)),
         "score": ramp(leftovers, 0, 5), "range": [0, 5]},
        {"name": "Commented-out code", "value": "%d blocks" % c.get("commented_code", 0), "score": ramp(c.get("commented_code", 0) / kloc, 0, 3), "range": [0, 3]},
        {"name": "Unwired plumbing", "value": "%d unsent keys, dead branches, unhandled events" % deadish, "score": ramp(deadish, 0, 15), "range": [0, 15]},
    ])

    total = sum(WEIGHTS[k] * cats[k]["score"] for k in WEIGHTS)
    tier = next(t for t in TIERS if total < t[0])
    return {"total": round(total, 1), "tier": tier[1], "tier_label": tier[2], "categories": cats, "weights": WEIGHTS,
            "version": VERSION, "kloc": round(kloc, 1)}
