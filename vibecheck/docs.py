"""Markdown and prompt files: what an agent reads before it reads the code.

Agent instruction files (CLAUDE.md, AGENTS.md, .cursorrules, ...) are loaded into every
session, so a stale sentence there misleads every agent that ever opens the repo. This module
extracts what later checks need: inline code references, fenced command blocks, sentences
with their headings, and prompt collections with whatever status the doc gives each prompt.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# How directly an agent consumes the file: 3 = loaded automatically, 2 = read first by habit.
AGENT_DOCS = {
    "claude.md": 3, "agents.md": 3, "gemini.md": 3, ".cursorrules": 3, ".windsurfrules": 3,
    "copilot-instructions.md": 3, ".clinerules": 3, "codex.md": 3,
    "readme.md": 2, "contributing.md": 2, "architecture.md": 2, "design.md": 1,
}
DOC_EXT = {"md", "markdown", "mdx", "rst", "txt"}

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_FENCE_RE = re.compile(r"^\s*(```+|~~~+)\s*([\w+-]*)")
_SPAN_RE = re.compile(r"`([^`\n]+)`")
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z`*(\"'])")
_PROMPT_HEAD_RE = re.compile(r"\bprompt\s+([A-Z]?\d+[a-z]?|[A-Z]\d*)\b[:\s—-]*(.*)", re.I)


@dataclass
class DocFacts:
    path: str
    agent_weight: int
    lines: int
    headings: List[Tuple[int, int, str]] = field(default_factory=list)
    spans: List[Tuple[str, int]] = field(default_factory=list)
    code_blocks: List[dict] = field(default_factory=list)
    sentences: List[Tuple[str, int, str]] = field(default_factory=list)
    table_rows: List[Tuple[List[str], int]] = field(default_factory=list)
    prompts: List[dict] = field(default_factory=list)
    is_prompt_file: bool = False
    item_starts: set = field(default_factory=set)    # lines where a list item begins: a rule never spans two items


def agent_weight(path: str) -> int:
    name = path.rsplit("/", 1)[-1].lower()
    w = AGENT_DOCS.get(name, 0)
    if w == 3 or (w and "/" not in path):
        return w
    if name in ("readme.md",) and path.count("/") >= 1:
        return 1
    return w or 1


def parse(path: str, src: str) -> DocFacts:
    lines = src.split("\n")
    df = DocFacts(path=path, agent_weight=agent_weight(path), lines=len(lines))
    ext = path.rsplit(".", 1)[-1].lower()
    if ext == "txt" or "/prompts/" in "/" + path.lower():
        df.is_prompt_file = "prompt" in path.lower()
        df.sentences = [(s.strip(), i + 1, "") for i, s in enumerate(lines) if s.strip()]
        return df
    heading = ""
    fence: Optional[dict] = None
    para: List[Tuple[int, str]] = []

    def flush() -> None:
        if not para:
            return
        text = " ".join(t.strip() for _, t in para)
        starts = []
        pos = 0
        for ln, t in para:
            starts.append((pos, ln))
            pos += len(t.strip()) + 1
        offset = 0
        for sent in _SENT_SPLIT.split(text):
            ln = para[0][0]
            for p, l in starts:
                if p <= offset:
                    ln = l
            if sent.strip():
                df.sentences.append((sent.strip(), ln, heading))
            offset += len(sent) + 1
        para.clear()

    for i, raw in enumerate(lines, 1):
        fm = _FENCE_RE.match(raw)
        if fence is not None:
            if fm and fm.group(1)[0] == fence["marker"][0]:
                df.code_blocks.append(fence)
                fence = None
            else:
                fence["text"] += raw + "\n"
            continue
        if fm:
            flush()
            fence = {"lang": fm.group(2).lower(), "text": "", "line": i, "heading": heading, "marker": fm.group(1)}
            continue
        hm = _HEADING_RE.match(raw)
        if hm:
            flush()
            heading = hm.group(2).strip()
            df.headings.append((i, len(hm.group(1)), heading))
            continue
        for m in _SPAN_RE.finditer(raw):
            df.spans.append((m.group(1).strip(), i))
        s = raw.strip()
        if s.startswith("|"):
            flush()
            cells = [c.strip() for c in s.strip("|").split("|")]
            if not all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                df.table_rows.append((cells, i))
                df.sentences.append((" ".join(cells), i, heading))
            continue
        if not s:
            flush()
            continue
        if re.match(r"^([-*+]|\d+[.)])\s+", s):
            flush()
            df.item_starts.add(i)
        para.append((i, re.sub(r"^([-*+>]|\d+[.)])\s+", "", s)))
    flush()

    # prompt collections: headings named "Prompt X", with a status from the doc's own tables
    heads = df.headings
    prompt_heads = [(ln, lvl, t) for ln, lvl, t in heads if _PROMPT_HEAD_RE.search(t)]
    if len(prompt_heads) >= 2 or "prompt" in path.lower():
        df.is_prompt_file = bool(prompt_heads)
        for idx, (ln, lvl, t) in enumerate(prompt_heads):
            m = _PROMPT_HEAD_RE.search(t)
            pid = m.group(1)
            end = len(lines)
            for ln2, lvl2, _ in heads:
                if ln2 > ln and lvl2 <= lvl:
                    end = ln2 - 1
                    break
            status = ""
            for cells, rln in df.table_rows:
                if cells and re.sub(r"[*_`]", "", cells[0]).strip().lower() == pid.lower():
                    status = re.sub(r"[*_`]", "", cells[-1]).strip()
            body_lines = end - ln + 1
            df.prompts.append({"id": pid, "title": t, "line": ln, "end": end, "lines": body_lines, "status": status})
        for sent, sln, _ in df.sentences:
            dm = re.search(r"do not run\s+((?:[\w]+(?:,\s*|\s+or\s+|\s+and\s+)?)+)", sent, re.I)
            if dm:
                ids = re.findall(r"[A-Z]?\d+[a-z]?", dm.group(1))
                for p in df.prompts:
                    if p["id"] in ids:
                        p["status"] = (p["status"] + "; " if p["status"] else "") + "do not run (line %d)" % sln
    return df


def classify_status(status: str) -> str:
    s = status.lower()
    if re.search(r"do not run|superseded|dead|void|obsolete|abandon|parked|replaced", s):
        return "dead"
    if re.search(r"\bpart\b.*\b(after|later|pending|todo|next)\b|partial|in progress|half", s):
        return "partial"
    if re.search(r"\bdone\b|built|committed|complete|shipped|merged", s):
        return "done"
    if s:
        return "open"
    return "unknown"
