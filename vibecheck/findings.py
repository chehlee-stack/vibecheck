"""A finding: one claim, its severity, and the lines that prove it."""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

SEVERITY_RANK = {"high": 3, "medium": 2, "low": 1, "info": 0}


def finding(fid: str, severity: str, category: str, title: str, detail: str,
            evidence: Sequence[Tuple[str, int]] = (), files: Optional[Sequence[str]] = None,
            data: Optional[dict] = None) -> dict:
    ev = [{"file": f, "line": ln} for f, ln in evidence]
    fs = list(dict.fromkeys(files if files is not None else [f for f, _ in evidence]))
    return {"id": fid, "severity": severity, "category": category, "title": title, "detail": detail,
            "evidence": ev, "files": fs, "data": data or {}}


def attach_source(findings: List[dict], sources: Dict[str, str], context: int = 0) -> None:
    """Fill each evidence entry with the text of its line, so the report never needs the repo."""
    split: Dict[str, List[str]] = {}
    for f in findings:
        for ev in f["evidence"]:
            src = sources.get(ev["file"])
            if src is None or not ev.get("line"):
                continue
            lines = split.setdefault(ev["file"], src.split("\n"))
            i = ev["line"] - 1
            if 0 <= i < len(lines):
                ev["text"] = lines[i].rstrip()[:240]


def sort_findings(findings: List[dict]) -> List[dict]:
    return sorted(findings, key=lambda f: (-SEVERITY_RANK.get(f["severity"], 0), f["category"], f["title"]))
