"""The score at every commit: how the debt built up, and what paid it down."""
from __future__ import annotations

import time
from collections import Counter
from pathlib import Path
from typing import List

from . import history as history_mod
from . import scan as scan_mod


def run(root: Path, ref: str = "HEAD", progress=None) -> List[dict]:
    commits = history_mod.load(root, ref)
    out = []
    for idx, c in enumerate(commits):
        t = time.time()
        s = scan_mod.run(root, c.sha, commits=commits[:idx + 1])
        code = [p for p, r in s.proj.roles.items() if r == "code"]
        sev = Counter(f["severity"] for f in s.findings)
        out.append({
            "sha": c.sha[:7], "ts": c.ts, "subject": c.subject, "ai": c.ai,
            "score": s.score["total"], "tier": s.score["tier_label"],
            "categories": {k: v["score"] for k, v in s.score["categories"].items()},
            "loc": sum(s.metrics[p]["loc"] for p in code), "files": len(code),
            "largest": max(((s.metrics[p]["loc"], p) for p in code), default=(0, ""))[1],
            "largest_loc": max((s.metrics[p]["loc"] for p in code), default=0),
            "top_risk": [{"path": r["path"], "score": r["score"]} for r in s.ranking[:5]],
            "findings": dict(sev),
            "high": [{"id": f["id"], "title": f["title"], "evidence": f["evidence"][:2]} for f in s.findings if f["severity"] == "high"][:12],
            "seconds": round(time.time() - t, 2),
        })
        if progress:
            progress(idx + 1, len(commits), c)
    return out
