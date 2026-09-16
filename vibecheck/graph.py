"""One weighted graph from four kinds of evidence.

  static    a references b (extends, class member, autoload, preload, import)   a -> b
  contract  a sends a string key that b's table routes                           a -> b
  string    a and b share rare string literals                                   a <-> b
  cochange  a and b changed in the same commits                                  a <-> b

Edges point from the dependent to the dependency. Impact runs the other way: when b changes,
everything with an edge into b may break. `impact_neighbors` gives that reversed view, with
the two symmetric kinds counted both ways.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .contracts import Contracts
from .history import HistoryStats
from .project import Project

KIND_WEIGHT = {"static": 1.0, "contract": 0.75, "string": 0.35, "cochange": 0.8}


@dataclass
class Graph:
    nodes: List[str]
    edges: Dict[Tuple[str, str], dict] = field(default_factory=dict)   # (src, dst) -> {w, kinds: {kind: w}, why: [..]}

    def add(self, src: str, dst: str, kind: str, w: float, why: str) -> None:
        if src == dst or w <= 0:
            return
        e = self.edges.setdefault((src, dst), {"w": 0.0, "kinds": {}, "why": []})
        prev = e["kinds"].get(kind, 0.0)
        e["kinds"][kind] = max(prev, w)
        e["w"] = min(1.5, sum(e["kinds"].values()))
        if why and why not in e["why"] and len(e["why"]) < 6:
            e["why"].append(why)

    def impact_neighbors(self) -> Dict[str, List[Tuple[str, float, dict]]]:
        """node -> [(neighbor that may break when node changes, weight, edge)]"""
        out: Dict[str, List[Tuple[str, float, dict]]] = defaultdict(list)
        for (src, dst), e in self.edges.items():
            out[dst].append((src, e["w"], e))
        return out

    def undirected(self) -> Dict[str, Dict[str, float]]:
        adj: Dict[str, Dict[str, float]] = defaultdict(dict)
        for (a, b), e in self.edges.items():
            adj[a][b] = max(adj[a].get(b, 0.0), e["w"])
            adj[b][a] = max(adj[b].get(a, 0.0), e["w"])
        return adj


def build(proj: Project, contracts: Contracts, history: Optional[HistoryStats]) -> Graph:
    nodes = [p for p, r in proj.roles.items() if r in ("code", "generated", "test", "tool", "resource")]
    ns = set(nodes)
    g = Graph(nodes=nodes)
    for (src, dst), e in proj.edges.items():
        if src in ns and dst in ns:
            members = sorted(e.members)
            name = dst.rsplit("/", 1)[-1]
            if members:
                why = "uses %s%s" % (", ".join(members[:4]), " +%d" % (len(members) - 4) if len(members) > 4 else "")
            else:
                why = {"extends": "extends %s", "preload": "preloads %s", "load": "loads %s", "autoload": "uses autoload %s",
                       "scene-script": "scene runs %s", "import": "imports %s", "include": "includes %s"}.get(e.kind, "references %s") % name
            w = KIND_WEIGHT["static"] * e.weight * (1.0 + min(0.4, 0.05 * len(members)))
            g.add(src, dst, "static", w, why)
    sends = defaultdict(set)
    for s in contracts.sends:
        tfile = s["table"].split("::")[0]
        if s["file"] != tfile and s["file"] in ns and tfile in ns:
            sends[(s["file"], tfile, s["table"].split("::")[1])].add(s["key"])
    for (src, dst, table), keys in sends.items():
        ks = sorted(keys)
        g.add(src, dst, "contract", KIND_WEIGHT["contract"] * min(1.0, 0.5 + 0.1 * len(ks)),
              "sends %s to %s" % (", ".join("'%s'" % k for k in ks[:4]) + (" +%d" % (len(ks) - 4) if len(ks) > 4 else ""), table))
    for (a, b), e in contracts.string_edges.items():
        if a in ns and b in ns and e["weight"] >= 0.6:
            w = KIND_WEIGHT["string"] * min(1.0, e["weight"] / 3.0)
            why = "shares strings %s" % ", ".join("'%s'" % s for s in e["strings"][:3])
            g.add(a, b, "string", w, why)
            g.add(b, a, "string", w, why)
    if history and history.commits >= 5:
        for (a, b), n in history.pair_count.items():
            if n < 2 or a not in ns or b not in ns:
                continue
            conf = max(history.confidence(a, b), history.confidence(b, a))
            # weighted by 1/(n-1) per commit: many sweeping commits count for less than a few focused ones
            w = KIND_WEIGHT["cochange"] * conf * min(1.0, history.pair_weight.get((a, b), 0.0) / 1.5)
            if w < 0.08:
                continue
            why = "changed together in %d commit%s" % (n, "" if n == 1 else "s")
            g.add(a, b, "cochange", w, why)
            g.add(b, a, "cochange", w, why)
    return g


def pagerank(g: Graph, nodes: List[str], damping: float = 0.85, iters: int = 60) -> Dict[str, float]:
    """Importance as a dependency: rank flows from each file to what it depends on (static and contract edges)."""
    out: Dict[str, List[Tuple[str, float]]] = defaultdict(list)
    ns = set(nodes)
    for (src, dst), e in g.edges.items():
        w = e["kinds"].get("static", 0.0) + e["kinds"].get("contract", 0.0)
        if w > 0 and src in ns and dst in ns:
            out[src].append((dst, w))
    n = len(nodes) or 1
    rank = {p: 1.0 / n for p in nodes}
    for _ in range(iters):
        new = {p: (1.0 - damping) / n for p in nodes}
        sink = 0.0
        for p in nodes:
            targets = out.get(p)
            if not targets:
                sink += rank[p]
                continue
            total = sum(w for _, w in targets)
            for q, w in targets:
                new[q] += damping * rank[p] * w / total
        for p in nodes:
            new[p] += damping * sink / n
        rank = new
    return rank
