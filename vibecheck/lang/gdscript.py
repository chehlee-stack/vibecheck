"""GDScript: a tokenizer and a single-pass extractor.

Not a parser. GDScript's indentation, `$Node` paths, `%Unique` names, `&"StringName"` and
`^"NodePath"` literals are all tokenized properly, and from the token stream we recover what
the coupling graph and the contract checks need: declarations and their spans, references to
other scripts' globals, resource paths, every call with a summary of its arguments, every
dictionary literal with its string keys, and every subscript by a string or a plain name.
"""
from __future__ import annotations

import re
from bisect import bisect_right
from typing import Dict, List, Optional, Set, Tuple

from ..facts import FileFacts, Func

_TOKEN_RE = re.compile(r"""
  (?P<nl>\n)
| (?P<ws>[ \t\r\f]+|\\\r?\n)
| (?P<comment>\#[^\n]*)
| (?P<tstr>[&^r]?(?:\"\"\"[\s\S]*?\"\"\"|'''[\s\S]*?'''))
| (?P<str>[&^r]?(?:"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'))
| (?P<nodepath>\$(?:"[^"\n]*"|'[^'\n]*'|%?[^\W\d][\w/%]*))
| (?P<annot>@[^\W\d]\w*)
| (?P<num>0[xX][0-9a-fA-F_]+|0[bB][01_]+|(?:\d[\d_]*(?:\.[\d_]*)?|\.\d[\d_]*)(?:[eE][+-]?\d+)?)
| (?P<name>[^\W\d]\w*)
| (?P<op>\*\*=|<<=|>>=|:=|->|==|!=|<=|>=|&&|\|\||\+=|-=|\*=|/=|%=|&=|\|=|\^=|\*\*|<<|>>|[-+*/%<>=!&|^~.,:;()\[\]{}?])
| (?P<other>.)
""", re.VERBOSE)

KEYWORDS = frozenset("""
if elif else for while match when break continue pass return class class_name extends is in as
self signal func static const enum var breakpoint preload await yield assert void and or not
true false null super PI TAU INF NAN
""".split())
_OPERAND_KEYWORDS = frozenset({"self", "true", "false", "null", "super", "PI", "TAU", "INF", "NAN"})
_OPEN = {"(", "[", "{"}
_CLOSE = {")", "]", "}"}
_COMPOUND = {"+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "**=", "<<=", ">>="}
_GENERATED_RE = re.compile(r"generated\s+(?:by|from|with)\s+`?([\w./\\-]+\.\w+)`?", re.I)

# (kind, value, line, pos)
Tok = Tuple[str, str, int, int]


_GROUP_KIND = ["", "nl", "ws", "comment", "tstr", "str", "nodepath", "annot", "num", "name", "op", "other"]


def tokenize(src: str) -> Tuple[List[Tok], List[Tuple[int, str]], Dict[int, int]]:
    """Tokens (without whitespace and comments), comments, and bracket depth at each line start."""
    toks: List[Tok] = []
    comments: List[Tuple[int, str]] = []
    line_depth: Dict[int, int] = {1: 0}
    line, depth = 1, 0
    append = toks.append
    for m in _TOKEN_RE.finditer(src):
        kind = _GROUP_KIND[m.lastindex]     # by index: lastgroup costs a dict lookup per token
        val = m[0]
        if kind == "nl":
            if depth == 0:
                append(("nl", "", line, m.start()))
            line += 1
            line_depth[line] = depth
        elif kind == "ws":
            if "\n" in val:
                line += 1
                line_depth[line] = depth + 1  # a backslash continuation never ends a block
        elif kind == "comment":
            comments.append((line, val))
        elif kind in ("tstr", "str"):
            prefix = val[0] if val[0] in "&^r" else ""
            body = val[len(prefix):]
            q = 3 if kind == "tstr" else 1
            tk = "strname" if prefix == "&" else "nodepath" if prefix == "^" else "str"
            append((tk, body[q:-q], line, m.start()))
            for _ in range(val.count("\n")):
                line += 1
                line_depth[line] = depth + 1
        elif kind == "nodepath":
            v = val[1:]
            if v[:1] in "\"'":
                v = v[1:-1]
            append(("nodepath", v, line, m.start()))
        elif kind == "op":
            if val in _OPEN:
                depth += 1
            elif val in _CLOSE:
                depth = max(0, depth - 1)
            append(("op", val, line, m.start()))
        elif kind in ("name", "num", "annot"):
            append((kind, val, line, m.start()))
        # "other": stray characters, ignored
    # `%Unique` is an operand only where an operand may start; after one it is modulo.
    out: List[Tok] = []
    i = 0
    while i < len(toks):
        t = toks[i]
        if t[0] == "op" and t[1] == "%" and i + 1 < len(toks):
            nxt = toks[i + 1]
            prev = out[-1] if out else None
            prev_is_operand = prev is not None and (
                prev[0] in ("num", "str", "strname", "nodepath")
                or (prev[0] == "name" and (prev[1] not in KEYWORDS or prev[1] in _OPERAND_KEYWORDS))
                or (prev[0] == "op" and prev[1] in _CLOSE)
            )
            if nxt[0] == "name" and nxt[3] == t[3] + 1 and not prev_is_operand:
                out.append(("nodepath", "%" + nxt[1], t[2], t[3]))
                i += 2
                continue
        out.append(t)
        i += 1
    return out, comments, line_depth


def _split(toks: List[Tok], match: Dict[int, int], start: int, end: int) -> List[Tuple[int, int]]:
    """Split toks[start:end] on top-level commas into (start, end) ranges."""
    parts, s, i = [], start, start
    while i < end:
        t = toks[i]
        if t[0] == "op" and t[1] in _OPEN and i in match:
            i = match[i] + 1
            continue
        if t[0] == "op" and t[1] == ",":
            parts.append((s, i))
            s = i + 1
        i += 1
    if s < end:
        parts.append((s, end))
    return [(a, b) for a, b in parts if a < b and not all(toks[k][0] == "nl" for k in range(a, b))]


def parse(path: str, src: str, globals_: Optional[Set[str]] = None) -> FileFacts:
    globals_ = globals_ or set()
    toks, comments, line_depth = tokenize(src)
    toks = [t for t in toks if t[0] != "nl"] + [("nl", "", 10**9, 10**9)] * 4  # lookahead sentinels
    lines = src.split("\n")
    n_lines = len(lines)
    ff = FileFacts(path=path, lang="gdscript", lines=n_lines)
    ff.comments = comments

    indent = [0] * (n_lines + 2)
    for ln, text in enumerate(lines, 1):
        w = 0
        for ch in text:
            if ch == "\t":
                w += 4
            elif ch == " ":
                w += 1
            else:
                break
        indent[ln] = w
    code_lines: List[int] = sorted({t[2] for t in toks if t[0] != "nl"})
    ff.loc = len(code_lines)
    first_tok_at: Dict[int, int] = {}
    for i, t in enumerate(toks):
        first_tok_at.setdefault(t[2], i)

    match: Dict[int, int] = {}
    stack: List[int] = []
    for i, t in enumerate(toks):
        if t[0] == "op":
            if t[1] in _OPEN:
                stack.append(i)
            elif t[1] in _CLOSE and stack:
                j = stack.pop()
                match[j] = i
                match[i] = j

    comment_at = {ln: text for ln, text in comments}

    def doc_above(ln: int) -> str:
        out = []
        k = ln - 1
        while k > 0 and k in comment_at and comment_at[k].startswith("##") and k not in first_tok_at:
            out.append(comment_at[k][2:].strip())
            k -= 1
        return " ".join(reversed(out)).strip()

    # ---- pass 1: declarations and function spans
    pending_annots: List[str] = []
    inner_classes: List[Tuple[str, int, int]] = []  # (name, line, indent)
    for i, (k, v, ln, _) in enumerate(toks):
        if k == "annot":
            name = v[1:]
            ff.annotations[name] = ff.annotations.get(name, 0) + 1
            pending_annots.append(name)
            continue
        if k != "name":
            continue
        nxt = toks[i + 1]
        prev = toks[i - 1] if i else None
        if prev is not None and prev[0] == "op" and prev[1] == ".":
            continue
        if v == "class_name" and nxt[0] == "name":
            ff.class_name = nxt[1]
        elif v == "extends" and indent[ln] == 0 and ff.extends is None and not (prev and prev[0] == "name" and prev[2] == ln and toks[i - 2][1] == "class"):
            if nxt[0] in ("name", "str"):
                ff.extends = nxt[1]
                if nxt[0] == "str":
                    ff.imports.append((nxt[1], ln, "extends"))
        elif v == "func" and nxt[0] == "name" and toks[i + 2][1] == "(":
            close = match.get(i + 2, i + 2)
            params = []
            for a, b in _split(toks, match, i + 3, close):
                if toks[a][0] == "name":
                    params.append(toks[a][1])
            owner = ""
            for cname, cline, cind in inner_classes:
                if cline < ln and indent[ln] > cind:
                    owner = cname
            ff.funcs.append(Func(name=nxt[1], line=ln, end=ln, params=params,
                                 static=bool(prev and prev[1] == "static" and prev[2] == ln),
                                 owner=owner, doc=doc_above(ln)))
            pending_annots = []
        elif v == "enum" and indent[ln] == 0 and (nxt[1] == "{" or toks[i + 2][1] == "{"):
            open_i = i + 1 if nxt[1] == "{" else i + 2
            close = match.get(open_i, open_i)
            if nxt[0] == "name":
                ff.symbols.append({"name": nxt[1], "kind": "enum", "line": ln, "exported": False, "onready": False, "doc": doc_above(ln)})
            for a, b in _split(toks, match, open_i + 1, close):
                if toks[a][0] == "name" and nxt[0] != "name":   # unnamed enum: members are the class's own names
                    ff.symbols.append({"name": toks[a][1], "kind": "enum value", "line": toks[a][2], "exported": False, "onready": False, "doc": ""})
            pending_annots = []
        elif v in ("var", "const", "signal", "class") and nxt[0] == "name":
            if v == "class":
                inner_classes.append((nxt[1], ln, indent[ln]))
            if indent[ln] == 0:
                ff.symbols.append({
                    "name": nxt[1], "kind": v, "line": ln,
                    "exported": any(a.startswith("export") for a in pending_annots),
                    "onready": "onready" in pending_annots,
                    "doc": doc_above(ln),
                })
                if v == "signal":
                    ff.signals.append((nxt[1], ln))
            pending_annots = []

    # function ends: the last code line indented deeper than the `func` line
    for f in ff.funcs:
        base = indent[f.line]
        end = f.line
        for ln in code_lines[bisect_right(code_lines, f.line):]:
            if line_depth.get(ln, 0) > 0:
                end = ln
                continue
            if indent[ln] <= base:
                break
            end = ln
        f.end = end
    for f in ff.funcs:
        if not f.owner:
            ff.symbols.append({"name": f.name, "kind": "static func" if f.static else "func", "line": f.line,
                               "exported": False, "onready": False, "doc": f.doc, "end": f.end, "params": f.params})
    ff.symbols.sort(key=lambda s: s["line"])

    func_at_line: Dict[int, str] = {}
    for f in sorted(ff.funcs, key=lambda f: -f.length):
        for ln in range(f.line, f.end + 1):
            func_at_line[ln] = f.name

    # file doc: the first ## block near the top
    for ln, text in comments[:40]:
        if text.startswith("##") and ln < 25:
            block, k = [], ln
            while k in comment_at and comment_at[k].startswith("##"):
                block.append(comment_at[k][2:].strip())
                k += 1
            ff.doc = " ".join(block).strip()
            break
    head = " ".join(text.lstrip("#").strip() for ln, text in comments[:8] if ln <= 12)
    gm = _GENERATED_RE.search(head)
    if gm and re.search(r"do\s+not\s+edit|don't\s+edit|re-?run", head, re.I):
        ff.generated_by = gm.group(1)

    # ---- helpers over the token stream
    def is_operand_end(t: Tok) -> bool:
        return (t[0] in ("name", "str", "strname", "nodepath", "num") and (t[0] != "name" or t[1] not in KEYWORDS or t[1] in _OPERAND_KEYWORDS)) \
            or (t[0] == "op" and t[1] in (")", "]"))

    def chain_before(j: int) -> str:
        """The receiver expression ending at token j, as text, for name(.name)* and f() chains."""
        parts: List[str] = []
        while j >= 0:
            k, v, _, _ = toks[j]
            if k == "name":
                parts.append(v)
            elif k == "op" and v == ")" and j in match and match[j] - 1 >= 0 and toks[match[j] - 1][0] == "name":
                j = match[j] - 1
                parts.append(toks[j][1] + "()")
            elif k == "op" and v == "]" and j in match:
                o = match[j]
                inner = toks[o + 1:j]
                if len(inner) == 1 and inner[0][0] == "str" and o - 1 >= 0 and is_operand_end(toks[o - 1]):
                    parts.append('["%s"]' % inner[0][1])
                    j = o - 1
                    # a subscript binds to what precedes it without a dot
                    k2, v2, _, _ = toks[j]
                    if k2 == "name":
                        parts.append(v2)
                    elif k2 == "op" and v2 == "]":
                        continue
                    else:
                        return "?"
                else:
                    return "?" if not parts else ".".join(reversed(parts))
            elif k == "nodepath":
                parts.append("$" + v)
                break
            else:
                break
            if j - 1 >= 0 and toks[j - 1][0] == "op" and toks[j - 1][1] == ".":
                j -= 2
                continue
            break
        text = ""
        for p in reversed(parts):
            text += p if (p.startswith("[") or not text) else "." + p
        return text or "?"

    def arg_summary(a: int, b: int) -> dict:
        if b - a == 1:
            t = toks[a]
            if t[0] in ("str", "strname"):
                return {"k": "str", "v": t[1]}
            if t[0] == "name":
                return {"k": "name", "v": t[1]}
            if t[0] == "nodepath":
                return {"k": "nodepath", "v": t[1]}
        if toks[a][0] == "op" and toks[a][1] == "{" and match.get(a) == b - 1:
            return {"k": "dict", "v": a}   # resolved to a dict index after the pass
        return {"k": "other", "v": None}

    dict_index_by_tok: Dict[int, int] = {}
    child_of: Dict[int, Tuple[int, str]] = {}
    sub_by_close: Dict[int, dict] = {}

    # ---- pass 2: references, calls, dictionaries, subscripts
    for i, (k, v, ln, _) in enumerate(toks):
        if k == "nl":
            continue
        fn_here = func_at_line.get(ln)
        prev = toks[i - 1] if i else ("nl", "", 0, 0)
        nxt = toks[i + 1]
        if k in ("str", "strname"):
            ff.strings.append((v, ln))
            continue
        if k == "nodepath":
            ff.node_paths.append((v, ln))
            continue
        if k == "name":
            after_dot = prev[0] == "op" and prev[1] == "."
            if not after_dot and (v[:1].isupper() or v in globals_) and v not in KEYWORDS:
                declared = prev[0] == "name" and prev[1] in ("class_name", "class", "enum", "const", "var", "func", "signal")
                if not declared:
                    member = toks[i + 2][1] if nxt[0] == "op" and nxt[1] == "." and toks[i + 2][0] == "name" else None
                    ff.refs.append((v, member, ln))
            if nxt[0] == "op" and nxt[1] == "(" and not (prev[0] == "name" and prev[1] in ("func", "signal")):
                close = match.get(i + 1)
                if close is None:
                    continue
                recv = chain_before(i - 2) if after_dot else None
                args = [arg_summary(a, b) for a, b in _split(toks, match, i + 2, close)]
                ff.calls.append({"fn": v, "recv": recv, "line": ln, "func": fn_here, "args": args})
                if v in ("preload", "load") and args and args[0]["k"] == "str" and (recv in (None, "ResourceLoader")):
                    ff.imports.append((args[0]["v"], ln, v))
                elif v == "emit" and recv and recv != "?":
                    ff.emits.append((recv.split(".")[-1], ln))
                elif v == "emit_signal" and args and args[0]["k"] == "str":
                    ff.emits.append((args[0]["v"], ln))
                elif v in ("call_deferred", "bind", "call") and recv and recv.endswith(".emit"):
                    ff.emits.append((recv[: -len(".emit")].split(".")[-1], ln))       # sig.emit.call_deferred()
                elif v in ("call_deferred", "call") and recv and recv.endswith("emit_signal") and args and args[0]["k"] == "str":
                    ff.emits.append((args[0]["v"], ln))                                # emit_signal.call_deferred("sig")
                elif v in ("call_deferred", "call") and len(args) >= 2 and args[0] == {"k": "str", "v": "emit_signal"} and args[1]["k"] == "str":
                    ff.emits.append((args[1]["v"], ln))                                # call_deferred("emit_signal", "sig")
                elif v == "connect":
                    if args and args[0]["k"] == "str":
                        ff.connects.append((recv, args[0]["v"], ln))
                    elif recv and recv != "?":
                        parts = recv.split(".")
                        ff.connects.append((".".join(parts[:-1]) or None, parts[-1], ln))
            elif v == "match" and nxt[0] == "name" and toks[i + 2][1] == ":" and fn_here:
                keys = []
                base = indent[ln]
                level = None
                for ln2 in code_lines[bisect_right(code_lines, ln):]:
                    if indent[ln2] <= base and line_depth.get(ln2, 0) == 0:
                        break
                    if level is None:
                        level = indent[ln2]
                    if indent[ln2] == level and ln2 in first_tok_at:
                        t2 = toks[first_tok_at[ln2]]
                        if t2[0] == "str":
                            keys.append((t2[1], ln2))
                if keys:
                    ff.matches.append({"func": fn_here, "param": nxt[1], "line": ln, "keys": keys})
            continue
        if k != "op":
            continue
        if v == "[" and i in match and is_operand_end(prev) and not (prev[0] == "name" and prev[1] in KEYWORDS and prev[1] not in _OPERAND_KEYWORDS):
            close = match[i]
            inner = toks[i + 1:close]
            after = toks[close + 1]
            mode = "write" if (after[0] == "op" and after[1] == "=") else "rw" if (after[0] == "op" and after[1] in _COMPOUND) else "read"
            if len(inner) == 1 and inner[0][0] in ("str", "strname"):
                key = inner[0][1]
                if prev[0] == "op" and prev[1] == "]" and (i - 1) in sub_by_close:
                    rec = sub_by_close.pop(i - 1)
                    rec["keys"].append(key)
                    rec["mode"] = mode
                    rec["line"] = ln
                else:
                    rec = {"base": chain_before(i - 1), "keys": [key], "line": ln, "mode": mode, "func": fn_here}
                    ff.subs.append(rec)
                sub_by_close[close] = rec
            elif len(inner) == 1 and inner[0][0] == "name":
                ff.name_subs.append({"base": chain_before(i - 1), "key": inner[0][1], "line": ln, "func": fn_here, "mode": mode})
        elif v == "{" and i in match:
            if prev[0] == "name" and (prev[1] == "enum" or (i >= 2 and toks[i - 2][1] == "enum")):
                continue
            close = match[i]
            keys = []
            for a, b in _split(toks, match, i + 1, close):
                sep = None
                j = a
                while j < b:
                    t = toks[j]
                    if t[0] == "op" and t[1] in _OPEN and j in match:
                        j = match[j] + 1
                        continue
                    if t[0] == "op" and t[1] in (":", "="):
                        sep = j
                        break
                    j += 1
                if sep is None:
                    continue
                key = None
                if sep - a == 1 and toks[a][0] in ("str", "strname") and toks[sep][1] == ":":
                    key = toks[a][1]
                elif sep - a == 1 and toks[a][0] == "name" and toks[sep][1] == "=":
                    key = toks[a][1]
                if key is None:
                    continue
                value_name = toks[sep + 1][1] if b - sep == 2 and toks[sep + 1][0] == "name" else None
                if toks[sep + 1][0] == "op" and toks[sep + 1][1] == "{" and match.get(sep + 1) == b - 1:
                    child_of[sep + 1] = (i, key)
                keys.append((key, toks[a][2], value_name))
            assigned = None
            j = i - 1
            if toks[j][0] == "op" and toks[j][1] in ("=", ":="):
                if j >= 4 and toks[j - 1][0] == "name" and toks[j - 2][1] == ":" and toks[j - 3][0] == "name" \
                        and toks[j - 4][1] in ("var", "const") and toks[j - 4][2] == ln:
                    assigned = toks[j - 3][1]
                elif toks[j - 1][0] == "name":
                    assigned = toks[j - 1][1]
                elif toks[j - 1][1] == "]":
                    assigned = chain_before(j - 1)
            elif toks[j][0] == "name" and toks[j][1] == "return":
                assigned = "return"
            parent = child_of.get(i)
            dict_index_by_tok[i] = len(ff.dicts)
            ff.dicts.append({"id": len(ff.dicts), "keys": keys, "assigned": assigned, "func": fn_here, "line": ln,
                             "parent": dict_index_by_tok.get(parent[0]) if parent else None,
                             "parent_key": parent[1] if parent else None})

    for c in ff.calls:
        for a in c["args"]:
            if a["k"] == "dict":
                a["v"] = dict_index_by_tok.get(a["v"])

    for t in toks:
        k, v = t[0], t[1]
        if k == "nl":
            continue
        if k == "name":
            ff.norm.append((v if v in KEYWORDS else "I", t[2]))
        elif k == "num":
            ff.norm.append(("N", t[2]))
        elif k in ("str", "strname"):
            ff.norm.append(("S", t[2]))
        elif k == "nodepath":
            ff.norm.append(("P", t[2]))
        else:
            ff.norm.append((v, t[2]))
    for s in ff.strings:
        if s[0].startswith("res://") and not any(imp[0] == s[0] and imp[1] == s[1] for imp in ff.imports):
            ff.imports.append((s[0], s[1], "ref"))
    return ff
