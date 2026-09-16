"""Python, through the standard library's own parser."""
from __future__ import annotations

import ast
import io
import keyword
import re
import tokenize as pytokenize
from typing import Dict, Optional

from ..facts import FileFacts, Func
from . import generic

_GENERATED_RE = re.compile(r"generated\s+(?:by|from|with)\s+`?([\w./\\-]+\.\w+)`?", re.I)


def _text(node: ast.AST) -> str:
    try:
        return ast.unparse(node)  # type: ignore[attr-defined]
    except Exception:
        return "?"


def parse(path: str, src: str) -> FileFacts:
    try:
        tree = ast.parse(src)
    except (SyntaxError, ValueError) as e:
        ff = generic.parse(path, src)
        ff.lang = "python"
        ff.parse_error = str(e)
        return ff
    ff = FileFacts(path=path, lang="python", lines=src.count("\n") + 1)
    ff.doc = (ast.get_docstring(tree) or "").split("\n\n")[0].replace("\n", " ")

    code_lines = set()
    try:
        for tok in pytokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == pytokenize.COMMENT:
                ff.comments.append((tok.start[0], tok.string))
            elif tok.type in (pytokenize.NL, pytokenize.NEWLINE, pytokenize.INDENT, pytokenize.DEDENT, pytokenize.ENDMARKER):
                continue
            else:
                for ln in range(tok.start[0], tok.end[0] + 1):
                    code_lines.add(ln)
                if tok.type == pytokenize.NAME:
                    ff.norm.append((tok.string if keyword.iskeyword(tok.string) else "I", tok.start[0]))
                elif tok.type == pytokenize.NUMBER:
                    ff.norm.append(("N", tok.start[0]))
                elif tok.type == pytokenize.STRING:
                    ff.norm.append(("S", tok.start[0]))
                else:
                    ff.norm.append((tok.string, tok.start[0]))
    except (pytokenize.TokenError, IndentationError):
        pass
    ff.loc = len(code_lines)
    head = " ".join(t.lstrip("#").strip() for ln, t in ff.comments if ln <= 12) + " " + ff.doc
    gm = _GENERATED_RE.search(head)
    if gm and re.search(r"do\s+not\s+edit|re-?run", head, re.I):
        ff.generated_by = gm.group(1)

    parents: Dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node

    def owner_class(node: ast.AST) -> str:
        p = parents.get(node)
        while p is not None:
            if isinstance(p, ast.ClassDef):
                return p.name
            if isinstance(p, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return ""
            p = parents.get(p)
        return ""

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            params = [a.arg for a in node.args.posonlyargs + node.args.args + node.args.kwonlyargs]
            ff.funcs.append(Func(name=node.name, line=node.lineno, end=getattr(node, "end_lineno", node.lineno) or node.lineno,
                                 params=params, owner=owner_class(node),
                                 doc=(ast.get_docstring(node) or "").split("\n\n")[0].replace("\n", " ")))
    ff.funcs.sort(key=lambda f: f.line)
    func_at_line: Dict[int, str] = {}
    for f in sorted(ff.funcs, key=lambda f: -f.length):
        for ln in range(f.line, f.end + 1):
            func_at_line[ln] = f.name

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            ff.symbols.append({"name": node.name, "kind": "class", "line": node.lineno, "exported": not node.name.startswith("_"),
                               "doc": (ast.get_docstring(node) or "").split("\n\n")[0], "end": getattr(node, "end_lineno", node.lineno)})
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            f = next(f for f in ff.funcs if f.line == node.lineno and f.name == node.name)
            ff.symbols.append({"name": node.name, "kind": "func", "line": node.lineno, "exported": not node.name.startswith("_"),
                               "doc": f.doc, "end": f.end, "params": f.params})
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                if isinstance(t, ast.Name):
                    ff.symbols.append({"name": t.id, "kind": "const" if t.id.isupper() else "var", "line": node.lineno,
                                       "exported": not t.id.startswith("_"), "doc": ""})

    dict_index: Dict[ast.AST, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            keys = []
            for k, v in zip(node.keys, node.values):
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    keys.append((k.value, k.lineno, v.id if isinstance(v, ast.Name) else None))
            p = parents.get(node)
            assigned: Optional[str] = None
            if isinstance(p, ast.Assign) and p.targets:
                assigned = _text(p.targets[0])
            elif isinstance(p, ast.AnnAssign):
                assigned = _text(p.target)
            elif isinstance(p, ast.Return):
                assigned = "return"
            parent_key = None
            parent_idx = None
            if isinstance(p, ast.Dict):
                for k, v in zip(p.keys, p.values):
                    if v is node and isinstance(k, ast.Constant) and isinstance(k.value, str):
                        parent_key = k.value
                parent_idx = dict_index.get(p)
            dict_index[node] = len(ff.dicts)
            ff.dicts.append({"id": len(ff.dicts), "keys": keys, "assigned": assigned, "func": func_at_line.get(node.lineno),
                             "line": node.lineno, "parent": parent_idx, "parent_key": parent_key})

    for node in ast.walk(tree):
        ln = getattr(node, "lineno", 0)
        if isinstance(node, ast.Import):
            for a in node.names:
                ff.imports.append((a.name, ln, "import"))
        elif isinstance(node, ast.ImportFrom):
            mod = "." * node.level + (node.module or "")
            ff.imports.append((mod, ln, "from"))
            if node.level and not node.module:
                for a in node.names:
                    ff.imports.append(("." * node.level + a.name, ln, "from"))
        elif isinstance(node, ast.Call):
            fn, recv = None, None
            if isinstance(node.func, ast.Name):
                fn = node.func.id
            elif isinstance(node.func, ast.Attribute):
                fn, recv = node.func.attr, _text(node.func.value)
            if fn:
                args = []
                for a in node.args:
                    if isinstance(a, ast.Constant) and isinstance(a.value, str):
                        args.append({"k": "str", "v": a.value})
                    elif isinstance(a, ast.Name):
                        args.append({"k": "name", "v": a.id})
                    elif isinstance(a, ast.Dict):
                        args.append({"k": "dict", "v": dict_index.get(a)})
                    else:
                        args.append({"k": "other", "v": None})
                ff.calls.append({"fn": fn, "recv": recv, "line": ln, "func": func_at_line.get(ln), "args": args})
        elif isinstance(node, ast.Subscript):
            sl = node.slice
            if isinstance(sl, ast.Index):  # Python < 3.9 AST shape
                sl = sl.value  # type: ignore[attr-defined]
            if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
                if isinstance(parents.get(node), ast.Subscript) and parents[node].value is node:
                    continue  # the outer subscript records the whole chain
                keys, base = [], node
                while isinstance(base, ast.Subscript):
                    s2 = base.slice.value if isinstance(base.slice, ast.Index) else base.slice  # type: ignore[attr-defined]
                    if isinstance(s2, ast.Constant) and isinstance(s2.value, str):
                        keys.append(s2.value)
                        base = base.value
                    else:
                        break
                p = parents.get(node)
                mode = "write" if isinstance(node.ctx, ast.Store) else "read"
                if isinstance(p, ast.AugAssign) and p.target is node:
                    mode = "rw"
                ff.subs.append({"base": _text(base), "keys": list(reversed(keys)), "line": ln, "mode": mode, "func": func_at_line.get(ln)})
            elif isinstance(sl, ast.Name):
                ff.name_subs.append({"base": _text(node.value), "key": sl.id, "line": ln, "func": func_at_line.get(ln),
                                     "mode": "write" if isinstance(node.ctx, ast.Store) else "read"})
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            ff.strings.append((node.value, ln))
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id[:1].isupper():
            p = parents.get(node)
            member = p.attr if isinstance(p, ast.Attribute) and p.value is node else None
            ff.refs.append((node.id, member, ln))
    return ff
