"""The facts every language adapter reports about one file.

Adapters differ in how well they fill this in (GDScript and Python are parsed; other
languages get a tokenizer and heuristics), but everything downstream — the coupling graph,
the contract checks, duplication, risk — only ever reads these fields.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class Func:
    name: str
    line: int
    end: int
    params: List[str]
    static: bool = False
    owner: str = ""          # enclosing class, for methods of inner classes
    doc: str = ""

    @property
    def length(self) -> int:
        return self.end - self.line + 1


@dataclass
class FileFacts:
    path: str
    lang: str
    lines: int = 0
    loc: int = 0                                   # lines holding code, not blanks or comments
    class_name: Optional[str] = None               # the global name this file declares, if any
    extends: Optional[str] = None
    doc: str = ""                                  # the file's own doc comment
    symbols: List[dict] = field(default_factory=list)       # {name, kind, line, exported, doc}
    funcs: List[Func] = field(default_factory=list)
    # (Name, member or None, line): references to names that may be another file's global.
    refs: List[Tuple[str, Optional[str], int]] = field(default_factory=list)
    # (spec, line, how): res:// paths, module names or relative imports, and how they were used.
    imports: List[Tuple[str, int, str]] = field(default_factory=list)
    # {fn, recv, line, func, args: [{k: str|name|dict|other, v}]}
    calls: List[dict] = field(default_factory=list)
    # {id, keys: [(key, line, value_name)], assigned, func, line, parent, parent_key}
    dicts: List[dict] = field(default_factory=list)
    # {base, keys, line, mode (read|write|rw), func}: subscripts with literal string keys
    subs: List[dict] = field(default_factory=list)
    # {base, key, line, func}: subscripts by a plain name, e.g. TABLE[kind]
    name_subs: List[dict] = field(default_factory=list)
    # {func, param, line, keys}: `match param:` with string patterns
    matches: List[dict] = field(default_factory=list)
    signals: List[Tuple[str, int]] = field(default_factory=list)
    emits: List[Tuple[str, int]] = field(default_factory=list)
    connects: List[Tuple[Optional[str], str, int]] = field(default_factory=list)
    node_paths: List[Tuple[str, int]] = field(default_factory=list)
    strings: List[Tuple[str, int]] = field(default_factory=list)
    comments: List[Tuple[int, str]] = field(default_factory=list)
    norm: List[Tuple[str, int]] = field(default_factory=list)   # normalized tokens, for clones
    annotations: Dict[str, int] = field(default_factory=dict)  # annotation name -> count
    generated_by: Optional[str] = None
    parse_error: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)       # adapter-specific facts (scenes, configs)

    def func_at(self, line: int) -> Optional[Func]:
        best = None
        for f in self.funcs:
            if f.line <= line <= f.end and (best is None or f.length < best.length):
                best = f
        return best
