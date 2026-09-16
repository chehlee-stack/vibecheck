"""Duplicated logic, found on normalized tokens.

Identifiers become `I`, numbers `N`, strings `S`, so a function copied and renamed still
matches. k-gram fingerprints are winnowed (Schleimer et al., 2003) to keep the index small,
then every shared fingerprint is extended both ways into the longest common run. Runs that
are mostly literals — data tables, colour lists — are dropped: that is data, not logic.
"""
from __future__ import annotations

import zlib
from collections import defaultdict
from typing import Dict, List, Set, Tuple

from .project import Project

K = 25            # tokens per fingerprint
W = 10            # winnowing window
MIN_TOKENS = 70
MIN_LINES = 6
MAX_OCC = 12      # a fingerprint in more places than this is a data table, not logic
BUDGET = 400_000  # extension steps; a pathological file cannot hang a scan
_BASE = 1000003
_MOD = (1 << 61) - 1


_IDS: Dict[str, int] = {}


def _token_id(t: str) -> int:
    """A token's id, derived from its text so the same code hashes alike in every file."""
    v = _IDS.get(t)
    if v is None:
        v = _IDS[t] = zlib.crc32(t.encode("utf-8")) + 1
    return v


def _fingerprints(seq: List[str]) -> List[Tuple[int, int]]:
    """Winnowed (hash, position) fingerprints, from a rolling hash over token ids.

    Ids come from the token's own text, not from Python's `hash()`: its per-process
    randomization would pick different fingerprints on every run — and, on self-similar
    files, occasionally a pathological number of them.
    """
    if len(seq) < K:
        return []
    xs = [_token_id(t) for t in seq]
    hashes: List[int] = []
    power = pow(_BASE, K - 1, _MOD)
    h = 0
    for i, x in enumerate(xs):
        if i < K:
            h = (h * _BASE + x) % _MOD
            if i == K - 1:
                hashes.append(h)
        else:
            h = ((h - xs[i - K] * power) * _BASE + x) % _MOD
            hashes.append(h)
    out: List[Tuple[int, int]] = []
    last = -1
    for start in range(0, max(1, len(hashes) - W + 1)):
        j = start
        best = hashes[start]
        for k in range(start + 1, min(start + W, len(hashes))):
            if hashes[k] <= best:
                best, j = hashes[k], k
        if j != last:
            out.append((hashes[j], j))
            last = j
    return out


def _dedup(pairs: List[dict]) -> List[dict]:
    """Different winnowed fingerprints inside the same repeated block extend into runs that
    nest inside one another — same start, growing end, once per fingerprint that survived
    winnowing. Keep only the maximal run per file pair; a contained run adds no information
    a human reading the larger one does not already have."""
    by_key: Dict[Tuple[str, str], List[dict]] = defaultdict(list)
    for d in pairs:
        by_key[(d["a"], d["b"])].append(d)
    out: List[dict] = []
    for group in by_key.values():
        group.sort(key=lambda d: -d["tokens"])
        kept: List[dict] = []
        for d in group:
            la, lb = d["a_lines"], d["b_lines"]
            if any(k["a_lines"][0] <= la[0] and la[1] <= k["a_lines"][1] and
                   k["b_lines"][0] <= lb[0] and lb[1] <= k["b_lines"][1] for k in kept):
                continue
            kept.append(d)
        out.extend(kept)
    return out


def detect(proj: Project, roles=("code", "test", "tool")) -> dict:
    files = [p for p, r in proj.roles.items() if r in roles and proj.files[p].norm and proj.files[p].lang not in ("godot-scene",)]
    toks: Dict[str, List[str]] = {p: [t for t, _ in proj.files[p].norm] for p in files}
    lines: Dict[str, List[int]] = {p: [ln for _, ln in proj.files[p].norm] for p in files}

    index: Dict[int, List[Tuple[str, int]]] = defaultdict(list)
    for p in files:
        for h, j in _fingerprints(toks[p]):
            index[h].append((p, j))

    covered: Dict[Tuple[str, str], Set[int]] = defaultdict(set)
    budget = [BUDGET]
    pairs: List[dict] = []
    for h, occ in sorted(index.items()):
        if len(occ) < 2 or len(occ) > MAX_OCC or budget[0] <= 0:
            continue
        for x in range(len(occ)):
            for y in range(x + 1, len(occ)):
                (pa, ia), (pb, ib) = occ[x], occ[y]
                if pa == pb and abs(ia - ib) < K:
                    continue
                if (pa, pb) > (pb, pa) or (pa == pb and ia > ib):
                    (pa, ia), (pb, ib) = (pb, ib), (pa, ia)
                key = (pa, pb)
                if ia >> 3 in covered[key] or budget[0] <= 0:
                    continue
                A, B = toks[pa], toks[pb]
                if A[ia:ia + K] != B[ib:ib + K]:
                    continue
                s_a, s_b = ia, ib
                while s_a > 0 and s_b > 0 and A[s_a - 1] == B[s_b - 1]:
                    s_a -= 1
                    s_b -= 1
                e_a, e_b = ia + K, ib + K
                while e_a < len(A) and e_b < len(B) and A[e_a] == B[e_b] and (pa != pb or e_a < s_b):
                    e_a += 1
                    e_b += 1
                budget[0] -= (e_a - s_a)
                covered[key].update(range(s_a >> 3, (e_a >> 3) + 1))
                n = e_a - s_a
                if n < MIN_TOKENS:
                    continue
                run = A[s_a:e_a]
                literal = sum(1 for t in run if t in ("S", "N", ",", ":", "[", "]", "{", "}", "(", ")")) / n
                if literal > 0.62 or len(set(run)) < 12:
                    continue
                la = (lines[pa][s_a], lines[pa][e_a - 1])
                lb = (lines[pb][s_b], lines[pb][e_b - 1])
                if la[1] - la[0] + 1 < MIN_LINES:
                    continue
                pairs.append({"a": pa, "a_lines": la, "b": pb, "b_lines": lb, "tokens": n})

    pairs = _dedup(pairs)
    pairs.sort(key=lambda d: -d["tokens"])
    dup_lines: Dict[str, Set[int]] = defaultdict(set)
    for d in pairs:
        for side in ("a", "b"):
            s, e = d[side + "_lines"]
            dup_lines[d[side]].update(range(s, e + 1))
    per_file = {p: len(v) for p, v in dup_lines.items()}
    return {"pairs": pairs, "dup_lines": per_file}
