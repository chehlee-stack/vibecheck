"""Godot's own files: project.godot, export presets, scenes and resources.

These hold the coupling a script cannot see from inside itself: which scripts are global
autoloads, which input actions exist, which files an export leaves out, and which method
names a scene's signal connections call by string.
"""
from __future__ import annotations

import re
from typing import Dict, List, Tuple

from ..facts import FileFacts

_SECTION_RE = re.compile(r"^\[([^\]]+)\]\s*$")
_KV_RE = re.compile(r"^([\w/.\-]+)\s*=\s*(.*)$")
_ATTR_RE = re.compile(r'(\w+)=("(?:[^"\\]|\\.)*"|[^\s\]]+)')


def _ini(src: str) -> List[Tuple[str, str, str, int]]:
    """(section, key, raw value, line) for Godot's INI dialect, joining multi-line values."""
    out = []
    section = ""
    lines = src.split("\n")
    i = 0
    while i < len(lines):
        raw = lines[i]
        s = raw.strip()
        if not s or s.startswith(";"):
            i += 1
            continue
        m = _SECTION_RE.match(s)
        if m and "=" not in s.split(" ", 1)[0]:
            section = m.group(1)
            i += 1
            continue
        kv = _KV_RE.match(s)
        if kv:
            key, value, start = kv.group(1), kv.group(2), i + 1
            depth = value.count("{") + value.count("[") - value.count("}") - value.count("]")
            while depth > 0 and i + 1 < len(lines):
                i += 1
                value += "\n" + lines[i]
                depth += lines[i].count("{") + lines[i].count("[") - lines[i].count("}") - lines[i].count("]")
            out.append((section, key, value.strip(), start))
        i += 1
    return out


def _unquote(v: str) -> str:
    v = v.strip()
    return v[1:-1] if len(v) >= 2 and v[0] == v[-1] == '"' else v


def parse_project(path: str, src: str) -> FileFacts:
    ff = FileFacts(path=path, lang="godot-project", lines=src.count("\n") + 1)
    autoloads: Dict[str, str] = {}
    actions: Dict[str, int] = {}
    warnings_off: List[str] = []
    main_scene = None
    for section, key, value, line in _ini(src):
        if section == "autoload":
            target = _unquote(value).lstrip("*")
            autoloads[key] = target
            ff.imports.append((target, line, "autoload"))
        elif section == "input":
            actions[key] = line
        elif section == "debug" and key.startswith("gdscript/warnings/"):
            name = key.split("/")[-1]
            if value in ("0", "false") and name not in ("enable",):
                warnings_off.append(name)
            if name == "enable" and value == "false":
                warnings_off.append("ALL")
        elif key == "run/main_scene":
            main_scene = _unquote(value)
            ff.imports.append((main_scene, line, "main_scene"))
        if value.startswith('"res://') or value.startswith('"*res://'):
            ff.strings.append((_unquote(value).lstrip("*"), line))
    ff.extra = {"autoloads": autoloads, "actions": actions, "warnings_off": warnings_off, "main_scene": main_scene}
    ff.loc = sum(1 for l in src.split("\n") if l.strip() and not l.strip().startswith(";"))
    return ff


def parse_export_presets(path: str, src: str) -> FileFacts:
    ff = FileFacts(path=path, lang="godot-export", lines=src.count("\n") + 1)
    presets: Dict[str, dict] = {}
    for section, key, value, line in _ini(src):
        if not section.startswith("preset.") or section.endswith(".options"):
            continue
        p = presets.setdefault(section, {"name": section, "exclude": [], "include": [], "filter": "", "line": line})
        if key == "name":
            p["name"] = _unquote(value)
        elif key in ("exclude_filter", "include_filter"):
            pats = [x.strip() for x in _unquote(value).split(",") if x.strip()]
            p["exclude" if key == "exclude_filter" else "include"] = pats
        elif key == "export_filter":
            p["filter"] = _unquote(value)
    for section, key, value, line in _ini(src):
        if section.endswith(".options") and key == "permissions/internet":
            presets.get(section[: -len(".options")], {}).setdefault("internet", value)
    ff.extra = {"presets": list(presets.values())}
    ff.loc = sum(1 for l in src.split("\n") if l.strip())
    return ff


def parse_scene(path: str, src: str) -> FileFacts:
    """.tscn / .tres: external resources, nodes and their scripts, and signal connections."""
    ff = FileFacts(path=path, lang="godot-scene", lines=src.count("\n") + 1)
    ext: Dict[str, Tuple[str, str]] = {}
    nodes: List[dict] = []
    connections: List[dict] = []
    current = None
    for ln, raw in enumerate(src.split("\n"), 1):
        s = raw.strip()
        if s.startswith("[") and s.endswith("]"):
            head = s[1:-1]
            kind = head.split(" ", 1)[0]
            attrs = {k: _unquote(v) for k, v in _ATTR_RE.findall(head)}
            current = None
            if kind == "ext_resource":
                ext[attrs.get("id", "")] = (attrs.get("type", ""), attrs.get("path", ""))
                if attrs.get("path"):
                    ff.imports.append((attrs["path"], ln, "ext_resource"))
            elif kind == "node":
                current = {"name": attrs.get("name", ""), "type": attrs.get("type", ""), "parent": attrs.get("parent"),
                           "line": ln, "script": None, "instance": None, "props": {}}
                inst = re.search(r'ExtResource\(\s*"?([^")]+)"?\s*\)', head)
                if inst and "instance=" in head:
                    current["instance"] = ext.get(inst.group(1), ("", ""))[1]
                nodes.append(current)
            elif kind == "connection":
                connections.append({"signal": attrs.get("signal"), "from": attrs.get("from"), "to": attrs.get("to"),
                                    "method": attrs.get("method"), "line": ln})
            continue
        if current is not None and "=" in s:
            key, _, value = s.partition("=")
            key, value = key.strip(), value.strip()
            m = re.match(r'ExtResource\(\s*"?([^")]+)"?\s*\)', value)
            if key == "script" and m:
                current["script"] = ext.get(m.group(1), ("", ""))[1]
            else:
                current["props"][key] = ln
        for m in re.finditer(r'"(res://[^"]+)"', s):
            ff.strings.append((m.group(1), ln))
    ff.extra = {"nodes": nodes, "connections": connections, "ext": {k: v[1] for k, v in ext.items()}}
    ff.loc = sum(1 for l in src.split("\n") if l.strip())
    return ff


def node_path(nodes: List[dict], node: dict) -> str:
    """A scene node's path from the root, as `[connection]` lines name it ("." is the root)."""
    if node.get("parent") is None:
        return "."
    parent = node["parent"]
    return node["name"] if parent == "." else parent + "/" + node["name"]
