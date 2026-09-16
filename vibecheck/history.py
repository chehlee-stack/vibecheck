"""What the commit log knows that the code does not: which files change together.

Static analysis sees an import. It cannot see that two files with no reference between them
have changed in the same commit nine times out of ten — the coupling that lives in a shared
string, a shared save format, or the author's head. Co-change recovers it. Each commit's
pairs are weighted 1/(n-1) so one sweeping commit cannot swamp a history of small ones.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

from .repo import git

MAX_COCHANGE_FILES = 30
FIX_RE = re.compile(r"\b(fix(es|ed|ing)?|bug|broke|broken|crash(es|ed)?|regression|revert(s|ed)?|hotfix|oops|typo|"
                    r"restore[sd]?|repair(s|ed)?|wrong|missing|again|undo|workaround)\b", re.I)
AI_RE = re.compile(r"co-authored-by:\s*([^<\n]*?(?:claude|codex|gpt|openai|copilot|cursor|devin|gemini|aider|jules|"
                   r"windsurf|cline|astra|amp|sweep|factory|augment|replit|lovable|bolt|v0)[^<\n]*)", re.I)
AI_BODY_RE = re.compile(r"generated with \[?(claude code|codex|cursor|aider|copilot)", re.I)


@dataclass
class Commit:
    sha: str
    ts: int
    author: str
    subject: str
    body: str
    files: Dict[str, Tuple[int, int]] = field(default_factory=dict)
    renames: List[Tuple[str, str]] = field(default_factory=list)

    @property
    def ai(self) -> Optional[str]:
        m = AI_RE.search(self.body)
        if m:
            return m.group(1).strip()
        m = AI_BODY_RE.search(self.body)
        return m.group(1) if m else None

    @property
    def fixlike(self) -> bool:
        return bool(FIX_RE.search(self.subject))


def _expand_rename(path: str) -> Tuple[str, str]:
    m = re.match(r"^(.*)\{(.*) => (.*)\}(.*)$", path)
    if m:
        pre, old, new, post = m.groups()
        return (re.sub("//+", "/", pre + old + post), re.sub("//+", "/", pre + new + post))
    old, new = path.split(" => ", 1)
    return old, new


def load(root: Path, ref: str = "HEAD", limit: int = 5000) -> List[Commit]:
    """Commits reachable from ref, oldest first, with per-file line counts (merges skipped)."""
    raw = git(root, "log", ref, "--no-merges", "-M", "--numstat", "-n", str(limit),
              "--format=%x1e%H%x1f%at%x1f%an%x1f%s%x1f%b%x1f", check=False)
    commits: List[Commit] = []
    for rec in raw.decode("utf-8", errors="replace").split("\x1e"):
        if not rec.strip():
            continue
        parts = rec.split("\x1f")
        if len(parts) < 6:
            continue
        sha, ts, author, subject, body, stat = parts[0].strip(), parts[1], parts[2], parts[3], parts[4], parts[5]
        c = Commit(sha=sha, ts=int(ts or 0), author=author, subject=subject.strip(), body=body.strip())
        for line in stat.strip().split("\n"):
            cols = line.split("\t")
            if len(cols) != 3:
                continue
            add, dele, path = cols
            if " => " in path:
                old, path = _expand_rename(path)
                c.renames.append((old, path))
            c.files[path] = (int(add) if add.isdigit() else 0, int(dele) if dele.isdigit() else 0)
        commits.append(c)
    commits.reverse()
    return commits


@dataclass
class HistoryStats:
    commits: int = 0
    first_ts: int = 0
    last_ts: int = 0
    changes: Counter = field(default_factory=Counter)        # path -> commits touching it
    churn: Counter = field(default_factory=Counter)          # path -> lines added + deleted
    fixes: Counter = field(default_factory=Counter)          # path -> fix-like commits touching it
    pair_count: Counter = field(default_factory=Counter)     # (a, b) -> commits changing both
    pair_weight: Dict[Tuple[str, str], float] = field(default_factory=lambda: defaultdict(float))
    pair_small: Counter = field(default_factory=Counter)     # (a, b) -> focused commits (<= 8 code files) changing both
    ai_commits: int = 0
    ai_agents: Counter = field(default_factory=Counter)
    docs_only_commits: int = 0
    fix_commits: int = 0
    median_code_files: float = 0.0
    median_code_lines: float = 0.0

    def confidence(self, a: str, b: str) -> float:
        """P(b changes | a changes)."""
        key = (a, b) if a < b else (b, a)
        return self.pair_count.get(key, 0) / self.changes[a] if self.changes.get(a) else 0.0

    def partners(self, path: str, min_count: int = 2, focused: int = 0) -> List[Tuple[str, int, float]]:
        """Files that change with `path`. `focused` requires that many of the shared commits to be
        small ones, so a sweeping restyle that touched every screen does not couple them all."""
        out = []
        for (a, b), n in self.pair_count.items():
            if n < min_count or path not in (a, b) or self.pair_small.get((a, b), 0) < focused:
                continue
            other = b if a == path else a
            out.append((other, n, n / max(1, self.changes[path])))
        return sorted(out, key=lambda t: (-t[1], -t[2]))


def analyze(commits: Iterable[Commit], code_paths: Set[str], doc_paths: Optional[Set[str]] = None) -> HistoryStats:
    commits = list(commits)
    st = HistoryStats(commits=len(commits))
    if not commits:
        return st
    st.first_ts, st.last_ts = commits[0].ts, commits[-1].ts
    # follow renames forward so old names count toward the file's current name
    alias: Dict[str, str] = {}
    for c in commits:
        for old, new in c.renames:
            alias[old] = new

    def current(p: str) -> str:
        seen = 0
        while p in alias and seen < 50:
            p = alias[p]
            seen += 1
        return p

    sizes, line_sizes = [], []
    for c in commits:
        if c.ai:
            st.ai_commits += 1
            st.ai_agents[re.sub(r"\s+", " ", c.ai)] += 1
        if c.fixlike:
            st.fix_commits += 1
        files = sorted({current(p) for p in c.files} & code_paths)
        if not files:
            if doc_paths is None or any(current(p) in doc_paths or p.endswith((".md", ".txt")) for p in c.files):
                st.docs_only_commits += 1
            continue
        lines = 0
        for p in c.files:
            cp = current(p)
            if cp in code_paths:
                a, d = c.files[p]
                st.churn[cp] += a + d
                lines += a + d
        sizes.append(len(files))
        line_sizes.append(lines)
        for f in files:
            st.changes[f] += 1
            if c.fixlike:
                st.fixes[f] += 1
        if 2 <= len(files) <= MAX_COCHANGE_FILES:
            w = 1.0 / (len(files) - 1)
            for a, b in combinations(files, 2):
                st.pair_count[(a, b)] += 1
                st.pair_weight[(a, b)] += w
                if len(files) <= 8:
                    st.pair_small[(a, b)] += 1
    if sizes:
        s = sorted(sizes)
        st.median_code_files = s[len(s) // 2]
        ls = sorted(line_sizes)
        st.median_code_lines = ls[len(ls) // 2]
    return st
