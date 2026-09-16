"""Boundaries the docs declare, checked against the dependency graph — transitively.

Agent instruction files state layering rules in prose: "`Wardrobe` and `Economy` are pure
functions. No `UI`, no `Game`." Architecture tests usually enforce such rules by searching
each file's text for the forbidden names, which proves only that the file does not name them
itself. A pure model that calls a helper that calls the UI still depends on the UI.

This reads each rule out of the prose (a paragraph that names project globals, and forbids
some of them with "no `X`" or "must not use `X`") and searches the static graph for a path
from every named file to every forbidden one.
"""
from __future__ import annotations

import re
from collections import deque
from typing import Dict, List, Optional, Tuple

from .findings import finding
from .project import Project

_FORBID_RE = re.compile(r"\b(?:no|not|never|without|must not (?:use|touch|call|reference|depend on|import)|"
                        r"do(?:es)? not (?:use|touch|call|reference|depend on|import)|may not (?:use|touch|call))\s+`([A-Za-z_]\w*)`", re.I)
_NAME_RE = re.compile(r"`([A-Za-z_]\w*)`")
_FOLLOW = {"class", "autoload", "extends", "preload", "load", "scene-script"}


def _paragraphs(df) -> List[Tuple[int, str]]:
    paras: List[Tuple[int, str]] = []
    last_line, last_heading = -10, None
    for sent, ln, heading in df.sentences:
        # a new list item starts a new rule; a second sentence on the same line continues the one it is in
        starts_item = ln in df.item_starts and ln != last_line
        if paras and heading == last_heading and ln - last_line <= 2 and not starts_item:
            paras[-1] = (paras[-1][0], paras[-1][1] + " " + sent)
        else:
            paras.append((ln, sent))
        last_line, last_heading = ln, heading
    return paras


def _path(proj: Project, start: str, goal: str) -> Optional[List[Tuple[str, str]]]:
    prev: Dict[str, str] = {start: ""}
    q = deque([start])
    while q:
        u = q.popleft()
        if u == goal:
            chain = []
            while prev[u]:
                chain.append((prev[u], u))
                u = prev[u]
            return list(reversed(chain))
        for e in proj.out_edges(u):
            if e.kind in _FOLLOW and e.dst not in prev and proj.roles.get(e.dst) in ("code", "generated"):
                prev[e.dst] = u
                q.append(e.dst)
    return None


def check(proj: Project) -> List[dict]:
    globals_ = dict(proj.classes)
    globals_.update(proj.autoloads)
    out: List[dict] = []
    for doc_path, df in proj.docs.items():
        if df.agent_weight < 2 or df.is_prompt_file:
            continue
        for line, text in _paragraphs(df):
            forbidden = [n for n in _FORBID_RE.findall(text) if n in globals_]
            if not forbidden:
                continue
            listed = [n for n in dict.fromkeys(_NAME_RE.findall(text)) if n in globals_ and n not in forbidden]
            if not listed:
                continue
            violations = []
            for d in listed:
                src = globals_[d]
                for f in dict.fromkeys(forbidden):
                    chain = _path(proj, src, globals_[f])
                    if chain:
                        violations.append((d, f, chain))
            if not violations:
                continue
            direct = [v for v in violations if len(v[2]) == 1]
            # one finding per named file, reporting the shortest route to each forbidden name
            by_src: Dict[str, List[Tuple[str, List[Tuple[str, str]]]]] = {}
            for d, f, chain in violations:
                by_src.setdefault(d, []).append((f, chain))
            for d, items in by_src.items():
                items.sort(key=lambda it: len(it[1]))
                f0, chain0 = items[0]
                hops = []
                evidence = [(doc_path, line)]
                for a, b in chain0:
                    e = proj.edges[(a, b)]
                    members = sorted(e.members)
                    hops.append("%s → %s%s" % (a.rsplit("/", 1)[-1], b.rsplit("/", 1)[-1], " (%s)" % ", ".join(members[:2]) if members else ""))
                    evidence.append((a, e.lines[0] if e.lines else 1))
                reached = ", ".join("`%s`" % f for f, _ in items)
                sev = "high" if len(chain0) == 1 else "medium"
                out.append(finding(
                    "boundary.documented-rule-violated", sev, "agent-risk",
                    "`%s` is declared free of %s, but reaches %s" % (d, ", ".join("`%s`" % f for f in dict.fromkeys(forbidden)), reached),
                    "%s:%d draws this boundary. The route: %s. %s" % (
                        doc_path, line, " → ".join([chain0[0][0].rsplit("/", 1)[-1]] + [b.rsplit("/", 1)[-1] for _, b in chain0]),
                        "A text search of the file for the forbidden names passes, because the file never names them; the dependency "
                        "arrives through a helper." if len(chain0) > 1 else "The file names it directly."),
                    evidence, data={"rule_doc": doc_path, "rule_line": line, "source": d, "reaches": [f for f, _ in items],
                                    "hops": hops}))
    return out
