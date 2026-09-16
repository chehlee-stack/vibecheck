"""Reading a repository at any commit, without checking anything out.

Every analysis in VibeCheck reads files through `Snapshot`, so a scan of HEAD, a scan of a
commit from last Tuesday, and the hundred scans a backtest makes all cost the same: one
`git ls-tree` and one `git cat-file --batch` per snapshot. A plain directory works too; it
just has no history.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

# Never worth reading as text.
BINARY_EXT = {
    "png", "jpg", "jpeg", "webp", "gif", "bmp", "ico", "icns", "tga", "exr", "hdr", "psd", "kra",
    "svgz", "ttf", "otf", "woff", "woff2", "eot", "mp3", "wav", "ogg", "flac", "mp4", "webm", "mov",
    "avi", "zip", "gz", "tar", "7z", "rar", "jar", "apk", "aab", "ipa", "exe", "dll", "so", "dylib",
    "a", "o", "pyc", "class", "bin", "dat", "db", "sqlite", "pdf", "import", "uid", "res", "scn",
    "mesh", "anim", "glb", "gltf", "fbx", "obj", "blend", "lock", "keystore", "jks",
}
# Directories that hold someone else's code or build output, not the project's.
VENDOR_DIRS = {
    ".git", ".godot", ".import", "node_modules", "__pycache__", ".venv", "venv", "env", "dist",
    "build", ".next", ".nuxt", "target", "vendor", "third_party", "addons", ".pytest_cache",
    ".mypy_cache", "coverage", ".turbo", ".cache", "Pods", "DerivedData",
}
MAX_TEXT_BYTES = 600_000


def git(repo: Path, *args: str, input_bytes: Optional[bytes] = None, check: bool = True) -> bytes:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and proc.returncode != 0:
        raise RuntimeError("git %s failed: %s" % (" ".join(args[:3]), proc.stderr.decode(errors="replace").strip()))
    return proc.stdout


def is_url(target: str) -> bool:
    return bool(re.match(r"^(https?://|git@|ssh://)", target)) or bool(re.match(r"^[\w.-]+/[\w.-]+$", target) and not os.path.exists(target))


def clone(target: str, workdir: Path) -> Path:
    """Clone a GitHub URL (or owner/name) into workdir, reusing an earlier clone."""
    m = re.search(r"github\.com[/:]([\w.-]+)/([\w.-]+?)(?:\.git)?/?$", target) or re.match(r"^([\w.-]+)/([\w.-]+)$", target)
    name = "%s__%s" % (m.group(1), m.group(2)) if m else re.sub(r"\W+", "_", target)[-60:]
    dest = workdir / name
    if (dest / ".git").exists():
        subprocess.run(["git", "-C", str(dest), "fetch", "--quiet", "--all"], stderr=subprocess.DEVNULL)
        return dest
    workdir.mkdir(parents=True, exist_ok=True)
    url = target if not re.match(r"^[\w.-]+/[\w.-]+$", target) else "https://github.com/%s.git" % target
    proc = subprocess.run(["git", "clone", "--quiet", url, str(dest)], stderr=subprocess.PIPE)
    if proc.returncode != 0 and m and shutil.which("gh"):
        # Private repositories: gh already holds the user's credentials.
        proc = subprocess.run(["gh", "repo", "clone", "%s/%s" % (m.group(1), m.group(2)), str(dest), "--", "--quiet"], stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise RuntimeError("could not clone %s: %s" % (target, proc.stderr.decode(errors="replace").strip()))
    return dest


@dataclass
class Snapshot:
    """The text files of a repository at one commit (or on disk, for a plain directory)."""

    root: Path
    ref: Optional[str]          # commit sha, or None for a working directory without git
    files: Dict[str, str] = field(default_factory=dict)   # path -> text
    skipped: Dict[str, str] = field(default_factory=dict) # path -> why
    all_paths: List[str] = field(default_factory=list)    # every tracked path, text or not

    @property
    def short(self) -> str:
        return self.ref[:7] if self.ref else "working-tree"


def _vendored(path: str) -> bool:
    return any(part in VENDOR_DIRS for part in path.split("/")[:-1])


def _ext(path: str) -> str:
    base = path.rsplit("/", 1)[-1]
    return base.rsplit(".", 1)[-1].lower() if "." in base else ""


def is_git_repo(root: Path) -> bool:
    return subprocess.run(["git", "-C", str(root), "rev-parse", "--git-dir"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


def resolve_ref(root: Path, ref: str = "HEAD") -> str:
    return git(root, "rev-parse", ref).decode().strip()


def snapshot(root: Path, ref: Optional[str] = "HEAD") -> Snapshot:
    root = Path(root)
    if ref is not None and is_git_repo(root):
        sha = resolve_ref(root, ref)
        listing = git(root, "ls-tree", "-r", "-z", "--long", sha).split(b"\0")
        wanted, snap = [], Snapshot(root=root, ref=sha)
        for entry in listing:
            if not entry:
                continue
            meta, path = entry.split(b"\t", 1)
            path_s = path.decode("utf-8", errors="replace")
            parts = meta.split()
            if parts[1] != b"blob":
                continue
            snap.all_paths.append(path_s)
            size = int(parts[3]) if parts[3] != b"-" else 0
            if _vendored(path_s):
                snap.skipped[path_s] = "vendored"
            elif _ext(path_s) in BINARY_EXT:
                snap.skipped[path_s] = "binary"
            elif size > MAX_TEXT_BYTES:
                snap.skipped[path_s] = "large"
            else:
                wanted.append(path_s)
        if wanted:
            request = "".join("%s:%s\n" % (sha, p) for p in wanted).encode()
            out = git(root, "cat-file", "--batch", input_bytes=request)
            pos = 0
            for p in wanted:
                nl = out.index(b"\n", pos)
                header = out[pos:nl].split()
                pos = nl + 1
                if len(header) < 3 or header[1] == b"missing":
                    continue
                size = int(header[2])
                blob = out[pos:pos + size]
                pos += size + 1
                if b"\0" in blob[:8000]:
                    snap.skipped[p] = "binary"
                    continue
                snap.files[p] = blob.decode("utf-8", errors="replace")
        return snap
    snap = Snapshot(root=root, ref=None)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in VENDOR_DIRS]
        for fn in filenames:
            full = Path(dirpath) / fn
            rel = full.relative_to(root).as_posix()
            snap.all_paths.append(rel)
            if _ext(rel) in BINARY_EXT:
                snap.skipped[rel] = "binary"
                continue
            try:
                if full.stat().st_size > MAX_TEXT_BYTES:
                    snap.skipped[rel] = "large"
                    continue
                data = full.read_bytes()
            except OSError:
                continue
            if b"\0" in data[:8000]:
                snap.skipped[rel] = "binary"
                continue
            snap.files[rel] = data.decode("utf-8", errors="replace")
    return snap
