"""Does the blast radius predict what actually changes? Ask the repository's own history.

For every commit that edits two or more existing code files, rebuild the world as it was just
before it: the code at its parent, and only the history older than it. Then predict, and
compare with what the commit really touched.

  seeded   Given the file the commit changed most, rank every other file. How many of the
           files the commit also had to change are in the top 5 / top 10?
  request  Given only the commit's subject line, as if it were a request typed into VibeCheck,
           find the seeds and rank. Same question, over every file the commit changed.

Baselines keep the numbers honest: a random ranking, files in the same directory, the static
graph alone, and co-change alone. A commit is one sample; a small history is a small sample,
and the report says how many there were.
"""
from __future__ import annotations

import random
import time
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional

from . import blast as blast_mod
from . import history as history_mod
from . import scan as scan_mod
from .repo import git

KS = (5, 10)


def _rank_ppr(scan, seeds: List[str], kinds: Optional[set], candidates: List[str]) -> List[str]:
    nbrs: Dict[str, list] = defaultdict(list)
    for (src, dst), e in scan.graph.edges.items():
        w = sum(v for k, v in e["kinds"].items() if kinds is None or k in kinds)
        if w > 0:
            nbrs[dst].append((src, w, e))
    passive = {p for p, r in scan.proj.roles.items() if r in ("test", "tool")}
    nbrs = {u: [(v, w, e) for v, w, e in lst if v not in passive] for u, lst in nbrs.items()}
    r = blast_mod._ppr(nbrs, seeds)
    cand = set(candidates) - set(seeds)
    ranked = sorted((p for p in cand if r.get(p, 0) > 0), key=lambda p: -r[p])
    return ranked


def _metrics(ranked: List[str], truth: set, n_candidates: int) -> dict:
    out = {}
    for k in KS:
        top = ranked[:k]
        out["recall@%d" % k] = len(set(top) & truth) / len(truth) if truth else 0.0
        out["hit@%d" % k] = 1.0 if set(top) & truth else 0.0
    rr = 0.0
    for i, p in enumerate(ranked):
        if p in truth:
            rr = 1.0 / (i + 1)
            break
    out["mrr"] = rr
    return out


def run(root: Path, ref: str = "HEAD", max_commits: int = 300, min_files: int = 2, max_files: int = 20,
        progress=None) -> dict:
    commits = history_mod.load(root, ref)
    rng = random.Random(7)
    samples = []
    t0 = time.time()
    for idx, c in enumerate(commits[-max_commits:]):
        idx = commits.index(c)
        if idx == 0:
            continue
        parent = git(root, "rev-parse", c.sha + "^", check=False).decode().strip()
        if not parent:
            continue
        try:
            before = scan_mod.run(root, parent, commits=commits[:idx], light=True)
        except Exception as e:  # a commit the parser cannot read should not end the backtest
            samples.append({"sha": c.sha[:7], "skipped": "scan failed: %s" % e})
            continue
        code = [p for p, r in before.proj.roles.items() if r in ("code", "generated")]
        codeset = set(code)
        changed = {p: a + d for p, (a, d) in c.files.items() if p in codeset}
        new_files = [p for p in c.files if p not in before.proj.files and p.endswith((".gd", ".py", ".ts", ".js", ".tscn"))]
        if len(changed) < min_files or len(changed) > max_files:
            continue
        seed = max(changed, key=lambda p: changed[p])
        truth = set(changed) - {seed}
        cands = [p for p in code if p != seed]
        sample = {"sha": c.sha[:7], "subject": c.subject, "ai": c.ai, "seed": seed, "truth": sorted(truth), "new_files": new_files,
                  "candidates": len(cands), "seeded": {}, "request": {}}
        methods = {
            "vibecheck": _rank_ppr(before, [seed], None, cands),
            "static only": _rank_ppr(before, [seed], {"static", "contract"}, cands),
            "co-change only": _rank_ppr(before, [seed], {"cochange"}, cands),
        }
        seed_dir = seed.rsplit("/", 1)[0] if "/" in seed else ""
        same_dir = sorted((p for p in cands if (p.rsplit("/", 1)[0] if "/" in p else "") == seed_dir),
                          key=lambda p: -before.metrics.get(p, {}).get("loc", 0))
        methods["same directory"] = same_dir
        shuffled = list(cands)
        rng.shuffle(shuffled)
        methods["random"] = shuffled
        for name, ranked in methods.items():
            # a method that ranks fewer files than k is padded at random, so every method is judged on k guesses
            padded = ranked + [p for p in shuffled if p not in set(ranked)]
            sample["seeded"][name] = _metrics(padded, truth, len(cands))
            if name == "vibecheck":
                sample["seeded_top"] = ranked[:10]

        # request mode: the subject line is the request
        truth_all = set(changed)
        q = blast_mod.parse_request(c.subject)
        index = {p: t for p, t in blast_mod._index(before).items() if p in codeset}
        bm = blast_mod.bm25(index, q["terms"])
        ordered = [p for p, _ in sorted(bm.items(), key=lambda kv: -kv[1][0])]
        top = bm[ordered[0]][0] if ordered else 0
        seeds = [p for p in ordered if bm[p][0] >= 0.4 * top][:3]
        if seeds:
            expanded = _rank_ppr(before, seeds, None, code)
            vib = seeds + [p for p in expanded if p not in seeds]
            vib += [p for p in ordered if p not in set(vib)]
        else:
            vib = []
        pad = list(code)
        rng.shuffle(pad)
        for name, ranked in (("vibecheck", vib), ("text match only", ordered), ("random", pad)):
            padded = ranked + [p for p in pad if p not in set(ranked)]
            sample["request"][name] = _metrics(padded, truth_all, len(code))
        sample["request_seeds"] = seeds
        samples.append(sample)
        if progress:
            progress(len(samples), c)

    scored = [s for s in samples if "seeded" in s]
    summary = {}
    for mode in ("seeded", "request"):
        agg = defaultdict(lambda: defaultdict(float))
        for s in scored:
            for method, m in s[mode].items():
                for k, v in m.items():
                    agg[method][k] += v
        summary[mode] = {method: {k: round(v / max(1, len(scored)), 3) for k, v in ms.items()} for method, ms in agg.items()}
    return {"commits_total": len(commits), "samples": len(scored), "skipped": [s for s in samples if "skipped" in s],
            "summary": summary, "details": scored, "seconds": round(time.time() - t0, 1),
            "method_note": "Seeded: the most-changed file is given; truth is the other existing code files the commit changed. "
                           "Request: only the subject line is given; truth is every existing code file it changed. "
                           "Each commit is scanned at its parent with only older history."}
