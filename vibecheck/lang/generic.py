"""Everything with C-like syntax: JavaScript and TypeScript, C#, shaders, Go, Rust, Swift...

A tokenizer and heuristics, not a parser. Enough for size, functions (by braces), imports,
string contracts and duplication; not enough for types. Languages that matter to a corpus
graduate to their own adapter, the way GDScript did.
"""
from __future__ import annotations

import re
from typing import Dict, List

from ..facts import FileFacts, Func

_TOKEN_RE = re.compile(r"""
  (?P<nl>\n)
| (?P<ws>[ \t\r\f]+)
| (?P<lcomment>//[^\n]*)
| (?P<bcomment>/\*[\s\S]*?\*/)
| (?P<pre>\#[ \t]*(?:include|import)[^\n]*)
| (?P<hcomment>\#[^\n]*)
| (?P<str>"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'|`(?:\\.|[^`\\])*`)
| (?P<num>0[xX][0-9a-fA-F_]+|(?:\d[\d_]*(?:\.[\d_]*)?|\.\d[\d_]*)(?:[eE][+-]?\d+)?[a-zA-Z]*)
| (?P<name>[^\W\d]\w*|\$\w*)
| (?P<op>=>|===|!==|==|!=|<=|>=|&&|\|\||\?\?|\?\.|\+=|-=|\*=|/=|::|->|\+\+|--|[-+*/%<>=!&|^~.,:;()\[\]{}?@])
| (?P<other>.)
""", re.VERBOSE)

_HASH_COMMENT_LANGS = {"rb", "sh", "bash", "zsh", "yaml", "yml", "toml", "r", "pl", "ex", "exs", "nim", "cr"}
_KEYWORDS = frozenset("""
if else for while do switch case default break continue return function class extends implements new
delete typeof instanceof in of var let const import export from as async await yield try catch finally
throw this super null undefined true false void static public private protected interface type enum
namespace module declare readonly abstract override package struct fn func impl pub mut use mod match
where trait self Self nil guard defer go chan select using object val fun when is sealed data lateinit
uniform varying in out inout float int vec2 vec3 vec4 mat4 sampler2D shader_type render_mode
""".split())
_JS_IMPORT_RE = re.compile(r"""(?:\bimport\s+(?:[\w*{}\s,$]+\s+from\s+)?|\bexport\s+[\w*{}\s,$]+\s+from\s+|\brequire\s*\(\s*|\bimport\s*\(\s*)["'`]([^"'`]+)["'`]""")
_INCLUDE_RE = re.compile(r"""\#\s*include\s*[<"]([^>"]+)[>"]""")
_GENERATED_RE = re.compile(r"generated\s+(?:by|from|with)\s+`?([\w./\\-]+\.\w+)`?", re.I)
_FUNC_PATTERNS = [
    re.compile(r"\bfunction\s*\*?\s*([A-Za-z_$][\w$]*)\s*\("),
    re.compile(r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:function\b|\([^()]*\)\s*(?::\s*[^=]+)?=>|[A-Za-z_$][\w$]*\s*=>)"),
    re.compile(r"^[ \t]*(?:(?:public|private|protected|internal|static|async|override|virtual|export|default|pub|fn|func|fun|def)[ \t]+)*(?:[\w<>\[\],.?*&]+[ \t]+)?([A-Za-z_]\w*)[ \t]*\([^;{}]*\)\s*(?::\s*[\w<>\[\],.? |]+)?\s*(?:->\s*[\w<>\[\],.? &]+)?\s*(?:throws\s+[\w, .]+)?\s*\{", re.M),
]
_NOT_FUNC_NAMES = {"if", "for", "while", "switch", "catch", "return", "function", "else", "do", "with", "using", "lock", "foreach", "sizeof"}


def parse(path: str, src: str) -> FileFacts:
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    ff = FileFacts(path=path, lang=ext or "text", lines=src.count("\n") + 1)
    code_lines = set()
    toks: List[tuple] = []
    line = 1
    for m in _TOKEN_RE.finditer(src):
        kind, val = m.lastgroup, m.group()
        if kind == "nl":
            line += 1
            continue
        if kind == "ws":
            continue
        if kind in ("lcomment", "bcomment") or (kind == "hcomment" and ext in _HASH_COMMENT_LANGS):
            ff.comments.append((line, val))
            line += val.count("\n")
            continue
        if kind == "pre":
            im = _INCLUDE_RE.search(val)
            if im:
                ff.imports.append((im.group(1), line, "include"))
            code_lines.add(line)
            continue
        if kind == "hcomment":
            kind, val = "op", "#"
        if kind == "str":
            ff.strings.append((val[1:-1], line))
            toks.append(("str", val[1:-1], line))
            ff.norm.append(("S", line))
            for k in range(val.count("\n") + 1):
                code_lines.add(line + k)
            line += val.count("\n")
            continue
        code_lines.add(line)
        toks.append((kind, val, line))
        if kind == "name":
            ff.norm.append((val if val in _KEYWORDS else "I", line))
        elif kind == "num":
            ff.norm.append(("N", line))
        elif kind == "op":
            ff.norm.append((val, line))
    ff.loc = len(code_lines)

    head = " ".join(c.strip("/*# \t") for ln, c in ff.comments if ln <= 12)
    gm = _GENERATED_RE.search(head)
    if gm and re.search(r"do\s+not\s+edit|re-?run|auto-?generated", head, re.I):
        ff.generated_by = gm.group(1)

    if ext in ("js", "jsx", "ts", "tsx", "mjs", "cjs", "mts", "cts", "vue", "svelte"):
        for m in _JS_IMPORT_RE.finditer(src):
            ff.imports.append((m.group(1), src.count("\n", 0, m.start()) + 1, "import"))

    # functions: a signature, then the brace block that follows it
    lines = src.split("\n")
    offsets = [0]
    for l in lines:
        offsets.append(offsets[-1] + len(l) + 1)
    seen = set()
    for pat in _FUNC_PATTERNS:
        for m in pat.finditer(src):
            name = m.group(1)
            if name in _NOT_FUNC_NAMES or name in _KEYWORDS:
                continue
            start_line = src.count("\n", 0, m.start()) + 1
            if (name, start_line) in seen:
                continue
            brace = src.find("{", m.end() - 1)
            arrow_expr = brace < 0 or src.count("\n", m.end(), brace) > 2
            if arrow_expr:
                end_line = start_line
            else:
                depth, j = 0, brace
                while j < len(src):
                    ch = src[j]
                    if ch == "{":
                        depth += 1
                    elif ch == "}":
                        depth -= 1
                        if depth == 0:
                            break
                    j += 1
                end_line = src.count("\n", 0, j) + 1
            seen.add((name, start_line))
            ff.funcs.append(Func(name=name, line=start_line, end=end_line, params=[]))
    ff.funcs.sort(key=lambda f: f.line)
    for f in ff.funcs:
        ff.symbols.append({"name": f.name, "kind": "func", "line": f.line, "exported": True, "doc": "", "end": f.end})

    # calls with a string first argument, and quoted-key object literals
    func_at_line: Dict[int, str] = {}
    for f in sorted(ff.funcs, key=lambda f: -f.length):
        for ln in range(f.line, f.end + 1):
            func_at_line[ln] = f.name
    for i, t in enumerate(toks[:-1]):
        if t[0] == "name" and toks[i + 1][1] == "(" and i + 2 < len(toks):
            recv = toks[i - 2][1] if i >= 2 and toks[i - 1][1] in (".", "?.") and toks[i - 2][0] == "name" else None
            a = toks[i + 2]
            args = [{"k": "str", "v": a[1]}] if a[0] == "str" and i + 3 < len(toks) and toks[i + 3][1] in (",", ")") else [{"k": "other", "v": None}]
            ff.calls.append({"fn": t[1], "recv": recv, "line": t[2], "func": func_at_line.get(t[2]), "args": args})
        elif t[0] == "op" and t[1] == "[" and i + 2 < len(toks) and toks[i + 1][0] == "str" and toks[i + 2][1] == "]" and i >= 1 and toks[i - 1][0] == "name":
            after = toks[i + 3][1] if i + 3 < len(toks) else ""
            ff.subs.append({"base": toks[i - 1][1], "keys": [toks[i + 1][1]], "line": t[2],
                            "mode": "write" if after == "=" else "read", "func": func_at_line.get(t[2])})
        elif t[0] == "name" and t[1][:1].isupper() and not (i >= 1 and toks[i - 1][1] in (".", "?.")):
            member = toks[i + 2][1] if toks[i + 1][1] == "." and i + 2 < len(toks) and toks[i + 2][0] == "name" else None
            ff.refs.append((t[1], member, t[2]))
    return ff
