"""One snapshot, parsed: every file's facts, the globals they share, and the static edges.

An edge `a -> b` means *a depends on b*: if b changes, a may break. Each edge keeps the lines
that justify it, so every claim the report makes can be traced to a line of code.
"""
from __future__ import annotations

import fnmatch
import hashlib
import posixpath
import re
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Set, Tuple

from . import docs as docs_mod
from .facts import FileFacts
from .lang import gdscript, generic, godot_res, python_lang
from .repo import Snapshot

CODE_EXT = {
    "gd": "gdscript", "py": "python", "js": "js", "jsx": "js", "ts": "ts", "tsx": "ts", "mjs": "js", "cjs": "js",
    "mts": "ts", "cts": "ts", "vue": "js", "svelte": "js", "cs": "csharp", "java": "java", "kt": "kotlin", "go": "go",
    "rs": "rust", "swift": "swift", "c": "c", "h": "c", "cpp": "cpp", "hpp": "cpp", "cc": "cpp", "m": "objc",
    "gdshader": "shader", "gdshaderinc": "shader", "glsl": "shader", "hlsl": "shader", "shader": "shader",
    "rb": "ruby", "php": "php", "dart": "dart", "lua": "lua", "scala": "scala", "sh": "shell", "bash": "shell",
}
RESOURCE_EXT = {"tscn", "tres"}
EDGE_WEIGHT = {
    "extends": 1.0, "scene-script": 1.0, "class": 0.8, "autoload": 0.8, "preload": 0.7, "load": 0.5,
    "import": 0.7, "include": 0.7, "ext_resource": 0.6, "ref": 0.4, "autoload-decl": 0.3, "main_scene": 0.3,
}
_TEST_RE = re.compile(r"(^|/)(tests?|specs?|__tests__|testing)(/|$)|(^|/)(test_[^/]*|[^/]*_test|[^/]*Tests?|[^/]*\.(test|spec))\.\w+$")


@dataclass
class Edge:
    src: str
    dst: str
    kind: str
    weight: float
    lines: List[int] = field(default_factory=list)
    members: Set[str] = field(default_factory=set)


@dataclass
class Project:
    snap: Snapshot
    files: Dict[str, FileFacts] = field(default_factory=dict)
    docs: Dict[str, docs_mod.DocFacts] = field(default_factory=dict)
    roles: Dict[str, str] = field(default_factory=dict)
    edges: Dict[Tuple[str, str], Edge] = field(default_factory=dict)
    godot_root: Optional[str] = None
    autoloads: Dict[str, str] = field(default_factory=dict)
    classes: Dict[str, str] = field(default_factory=dict)
    export_excludes: List[str] = field(default_factory=list)
    broken_paths: List[dict] = field(default_factory=list)
    languages: Dict[str, int] = field(default_factory=dict)

    # ------------------------------------------------------------------ helpers
    def res_to_path(self, spec: str) -> Optional[str]:
        if not spec.startswith("res://"):
            return None
        return posixpath.normpath((self.godot_root or "") + spec[6:]) if self.godot_root is not None else spec[6:]

    def code_files(self, include_tests: bool = True) -> List[str]:
        return [p for p, r in self.roles.items() if r in ("code", "generated") or (include_tests and r == "test")]

    def out_edges(self, path: str) -> List[Edge]:
        return [e for (s, _), e in self.edges.items() if s == path]

    def in_edges(self, path: str) -> List[Edge]:
        return [e for (_, d), e in self.edges.items() if d == path]

    def add_edge(self, src: str, dst: str, kind: str, line: int, member: Optional[str] = None) -> None:
        if src == dst or dst not in self.files:
            return
        key = (src, dst)
        e = self.edges.get(key)
        w = EDGE_WEIGHT.get(kind, 0.4)
        if e is None:
            e = self.edges[key] = Edge(src, dst, kind, w)
        elif w > e.weight:
            e.kind, e.weight = kind, w
        if line and line not in e.lines:
            e.lines.append(line)
        if member:
            e.members.add(member)

    def export_excluded(self, path: str) -> bool:
        rel = path[len(self.godot_root or ""):]
        return any(fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(rel, pat.rstrip("*") + "*") for pat in self.export_excludes)


def _ext(path: str) -> str:
    base = path.rsplit("/", 1)[-1]
    return base.rsplit(".", 1)[-1].lower() if "." in base else ""


# Scanning a history re-parses the same unchanged blobs at every commit; content-addressed
# facts make a 40-commit timeline cost about as much as two scans.
_CACHE: "OrderedDict[tuple, FileFacts]" = OrderedDict()
_CACHE_MAX = 8000


def _parse_cached(path: str, src: str, key_extra: tuple, build: Callable[[], FileFacts]) -> FileFacts:
    key = (path, hashlib.blake2b(src.encode("utf-8", "replace"), digest_size=12).digest()) + key_extra
    hit = _CACHE.get(key)
    if hit is not None:
        _CACHE.move_to_end(key)
        return hit
    ff = build()
    _CACHE[key] = ff
    while len(_CACHE) > _CACHE_MAX:
        _CACHE.popitem(last=False)
    return ff


def load(snap: Snapshot) -> Project:
    proj = Project(snap=snap)
    all_paths = set(snap.all_paths)

    godot_files = sorted((p for p in snap.files if p.rsplit("/", 1)[-1] == "project.godot"), key=len)
    if godot_files:
        gp = godot_files[0]
        proj.godot_root = gp[: -len("project.godot")]
        pf = godot_res.parse_project(gp, snap.files[gp])
        proj.files[gp] = pf
        proj.roles[gp] = "config"
        for name, target in pf.extra["autoloads"].items():
            path = proj.res_to_path(target)
            if path:
                proj.autoloads[name] = path
        ep = proj.godot_root + "export_presets.cfg"
        if ep in snap.files:
            ef = godot_res.parse_export_presets(ep, snap.files[ep])
            proj.files[ep] = ef
            proj.roles[ep] = "config"
            excl: List[str] = []
            for preset in ef.extra["presets"]:
                for pat in preset["exclude"]:
                    if pat not in excl:
                        excl.append(pat)
            proj.export_excludes = excl

    globals_ = set(proj.autoloads)
    for path, src in snap.files.items():
        ext = _ext(path)
        name = path.rsplit("/", 1)[-1]
        if path in proj.files:
            continue
        if ext in docs_mod.DOC_EXT or name.lower() in docs_mod.AGENT_DOCS:
            proj.docs[path] = docs_mod.parse(path, src)
            continue
        gkey = tuple(sorted(globals_))
        try:
            if ext == "gd":
                ff = _parse_cached(path, src, gkey, lambda: gdscript.parse(path, src, globals_))
            elif ext == "py":
                ff = _parse_cached(path, src, (), lambda: python_lang.parse(path, src))
            elif ext in RESOURCE_EXT and proj.godot_root is not None:
                ff = _parse_cached(path, src, (), lambda: godot_res.parse_scene(path, src))
            elif ext in CODE_EXT:
                ff = _parse_cached(path, src, (), lambda: generic.parse(path, src))
            else:
                continue
        except RecursionError as e:  # pathological nesting; keep going
            ff = FileFacts(path=path, lang=ext, lines=src.count("\n") + 1, parse_error=str(e))
        proj.files[path] = ff
        proj.languages[ff.lang] = proj.languages.get(ff.lang, 0) + ff.loc
        if ff.class_name:
            proj.classes.setdefault(ff.class_name, path)

    for path, ff in proj.files.items():
        if path in proj.roles:
            continue
        if ff.lang.startswith("godot-"):
            proj.roles[path] = "resource"
        elif ff.generated_by:
            proj.roles[path] = "generated"
        elif _TEST_RE.search(path):
            proj.roles[path] = "test"
        elif (proj.godot_root is not None and proj.export_excludes and proj.export_excluded(path)) or \
                re.match(r"^(tools|scripts/dev|dev|bin|devtools|hack)/", path[len(proj.godot_root or ""):]):
            proj.roles[path] = "tool"
        else:
            proj.roles[path] = "code"

    _resolve(proj, all_paths)
    return proj


def _resolve(proj: Project, all_paths: Set[str]) -> None:
    py_modules: Dict[str, str] = {}
    for p in proj.files:
        if p.endswith(".py"):
            mod = p[:-3].replace("/", ".")
            if mod.endswith(".__init__"):
                mod = mod[: -len(".__init__")]
            py_modules[mod] = p

    dirs: Set[str] = set()
    for p in all_paths:
        d = posixpath.dirname(p)
        while d and d not in dirs:
            dirs.add(d)
            d = posixpath.dirname(d)

    for path, ff in proj.files.items():
        own = ff.class_name
        # paths the file checks for before using are optional by design, not broken
        exist_calls = [c for c in ff.calls if c["fn"] in ("exists", "file_exists", "dir_exists", "dir_exists_absolute", "has_file")]
        guarded = {a["v"] for c in exist_calls for a in c["args"][:1] if a["k"] == "str"}
        guards_variables = any(c["args"] and c["args"][0]["k"] != "str" for c in exist_calls)
        if ff.lang == "gdscript":
            for name, member, line in ff.refs:
                if name == own:
                    continue
                if name in proj.autoloads:
                    proj.add_edge(path, proj.autoloads[name], "autoload", line, member)
                elif name in proj.classes:
                    kind = "extends" if ff.extends == name and not member else "class"
                    proj.add_edge(path, proj.classes[name], kind, line, member)
        for spec, line, how in ff.imports:
            target = None
            if spec.startswith("res://"):
                target = proj.res_to_path(spec)
                exists = bool(target) and (target in all_paths or target in dirs or target == ".")
                dynamic = "%" in spec or "{" in spec or spec.endswith("/")
                optional = spec in guarded or (how == "ref" and guards_variables)
                if target and not exists and not dynamic and not optional:
                    proj.broken_paths.append({"file": path, "line": line, "path": spec, "how": how})
                if how == "ext_resource" and target and target.endswith(".gd"):
                    how = "scene-script"
                if how == "extends":
                    how = "extends"
            elif ff.lang == "python":
                base_dir = posixpath.dirname(path)
                if spec.startswith("."):
                    level = len(spec) - len(spec.lstrip("."))
                    rel = spec.lstrip(".").replace(".", "/")
                    d = base_dir
                    for _ in range(level - 1):
                        d = posixpath.dirname(d)
                    for cand in (posixpath.join(d, rel) + ".py", posixpath.join(d, rel, "__init__.py")):
                        if cand in proj.files:
                            target = cand
                            break
                else:
                    cands = [spec, (base_dir.replace("/", ".") + "." + spec) if base_dir else spec]
                    for c in cands:
                        parts = c.split(".")
                        for k in range(len(parts), 0, -1):
                            if ".".join(parts[:k]) in py_modules:
                                target = py_modules[".".join(parts[:k])]
                                break
                        if target:
                            break
                how = "import"
            elif how in ("import", "include"):
                base_dir = posixpath.dirname(path)
                if spec.startswith("."):
                    stem = posixpath.normpath(posixpath.join(base_dir, spec))
                    for cand in [stem] + [stem + e for e in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".vue", ".svelte")] + \
                            [stem + "/index" + e for e in (".ts", ".tsx", ".js", ".jsx")]:
                        if cand in proj.files:
                            target = cand
                            break
                elif how == "include":
                    cand = posixpath.normpath(posixpath.join(base_dir, spec))
                    target = cand if cand in proj.files else None
            if target and target in proj.files:
                proj.add_edge(path, target, how, line)
        if ff.lang == "godot-scene":
            for node in ff.extra.get("nodes", []):
                if node.get("script"):
                    t = proj.res_to_path(node["script"])
                    if t:
                        proj.add_edge(path, t, "scene-script", node["line"])
