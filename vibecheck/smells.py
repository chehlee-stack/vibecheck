"""The fingerprints agentic development leaves behind.

Some are hazards in the code: an elision marker (`# ... rest of the code unchanged`) where an
agent replaced a block with a promise, chat-transcript voice in a comment, a stub that was
never filled, warnings silenced wholesale. Some live in the docs agents read first: a
reference to a file or function that no longer exists, a prompt marked dead that is still
sitting where an agent will find it. VibeCheck calls the second kind prompt archaeology.
"""
from __future__ import annotations

import fnmatch
import posixpath
import re
from collections import Counter, defaultdict
from typing import Dict, List, Set

from . import docs as docs_mod
from .contracts import ENGINE_MEMBERS, _declared_members
from .findings import finding
from .project import CODE_EXT, Project

ELISION_RE = re.compile(
    r"(\.\.\.\s*(existing|rest of|remaining|other|previous|unchanged)\b|"
    r"\b(existing|rest of the|remaining|other) (code|implementation|methods|functions|logic|file)\b.{0,20}"
    r"(\.\.\.|unchanged|here|goes here|remains|as before|stays the same)|"
    r"\b(same as (before|above)|unchanged from (before|above)|code omitted|omitted for brevity)\b)", re.I)
CHAT_RE = re.compile(
    r"\b(in a real (game|app|application|implementation|project|world|scenario)|for (simplicity|brevity|demonstration purposes)|"
    r"simplified (version|implementation|example)|you (can|could|may|might|would)( want to)? (add|replace|extend|implement|customi[sz]e)|"
    r"as an ai\b|here('s| is) (the|an?|your) (updated|complete|full|revised|new)|i('ve| have) (added|updated|changed|fixed)|"
    r"let me know|hope this helps)", re.I)
STUB_RE = re.compile(r"\b(placeholder|stub(bed)?|not (yet )?implemented|implement (this|me)|dummy (data|value|implementation)|"
                     r"fake (data|implementation)|hard-?coded for now|temporary (hack|fix|workaround))\b", re.I)
TODO_RE = re.compile(r"\b(TODO|FIXME|XXX|HACK)\b")
CODEISH_RE = re.compile(r"^\s*(func |var |const |if .*:$|elif |else:$|for .* in .*:|while .*:|return\b|match .*:|await |"
                        r"print\(|[\w.\[\]\"]+\s*[-+*/]?=\s*\S|[\w.]+\(.*\)\s*$|def |import |class \w+|let |function )")
VERSIONED_RE = re.compile(r"(^|[_\-. ])(old|new|v\d+|copy|backup|bak|final|tmp|temp|orig|deprecated|unused)([_\-. ]|$)|\(\d\)", re.I)
REMOVAL_RE = re.compile(r"\b(remov\w*|gone|delet\w*|dead|no longer|used to|replac\w*|renam\w*|retired|void|was|were|former\w*|"
                        r"previous\w*|old|historical|absent|dropped|parked|superseded|abandon\w*|becomes|became|moved|"
                        r"turned into|split|instead of|rather than|scrapped|cut|killed|dies|died|new)\b", re.I)


def _gitignore_matcher(proj: Project):
    pats = []
    for path, src in proj.snap.files.items():
        if path.rsplit("/", 1)[-1] != ".gitignore":
            continue
        base = posixpath.dirname(path)
        for raw in src.split("\n"):
            s = raw.strip()
            if not s or s.startswith("#") or s.startswith("!"):
                continue
            anchored = s.startswith("/") or "/" in s.rstrip("/")
            pats.append((base, s.strip("/"), anchored, s.endswith("/")))

    def ignored(rel: str) -> bool:
        rel = rel.strip("/")
        for base, pat, anchored, is_dir in pats:
            target = rel[len(base) + 1:] if base and rel.startswith(base + "/") else (rel if not base else None)
            if target is None:
                continue
            cands = [target] + ([] if anchored else [target.rsplit("/", 1)[-1]])
            parts = target.split("/")
            prefixes = ["/".join(parts[:i]) for i in range(1, len(parts) + 1)]
            for cand in cands + prefixes:
                if fnmatch.fnmatch(cand, pat) or fnmatch.fnmatch(cand, pat.replace("**/", "")):
                    return True
        return False
    return ignored
_PATHLIKE_RE = re.compile(r"^[\w.\-]+(/[\w.\-]+)+/?$|^[\w\-]+\.(gd|py|ts|tsx|js|jsx|cs|tscn|tres|gdshader|go|rs|swift|kt)$")
# A doc naming a source file that is gone is drift. A doc naming `report.html` or `data.json` is
# usually describing an output, and an asset path is the art pipeline's business, not the code's.
_SOURCE_EXT = {"gd", "py", "ts", "tsx", "js", "jsx", "mjs", "cjs", "cs", "tscn", "tres", "gdshader", "gdshaderinc",
               "go", "rs", "swift", "kt", "java", "rb", "php", "c", "cpp", "h", "hpp", "lua", "dart", "sh"}
_IDENT_REF_RE = re.compile(r"^([A-Z][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)(\(\))?$")


def analyze(proj: Project, contracts=None, history=None) -> dict:
    out: List[dict] = []
    counts = Counter()
    per_file: Dict[str, Counter] = defaultdict(Counter)
    code_roles = ("code", "generated", "tool", "test")

    for path, ff in proj.files.items():
        role = proj.roles.get(path)
        if role not in code_roles:
            continue
        comment_block: List[int] = []
        commented_code_blocks = 0
        for ln, text in ff.comments + [(10**9, "")]:
            body = text.lstrip("#/*! \t").strip()
            if ELISION_RE.search(body):
                out.append(finding("smell.elision-marker", "high", "prompt-archaeology",
                                   "Elision marker in %s" % path.rsplit("/", 1)[-1],
                                   "A comment that stands in for code (“rest of the code unchanged”). When an agent writes this into "
                                   "a file, the code it stood for is usually gone.", [(path, ln)]))
                counts["elision"] += 1
                per_file[path]["elision"] += 1
            if CHAT_RE.search(body):
                out.append(finding("smell.chat-voice", "medium", "prompt-archaeology",
                                   "Chat-transcript voice in a comment in %s" % path.rsplit("/", 1)[-1],
                                   "Text addressed to a person in a chat, pasted into the code with the answer.", [(path, ln)]))
                counts["chat"] += 1
                per_file[path]["chat"] += 1
            if STUB_RE.search(body) and role != "test":
                counts["stub"] += 1
                per_file[path]["stub"] += 1
                out.append(finding("smell.stub", "low", "prompt-archaeology", "Stub or placeholder note in %s" % path.rsplit("/", 1)[-1],
                                   "A promise to finish something later. Agents treat these as instructions or as done.", [(path, ln)]))
            if TODO_RE.search(text):
                counts["todo"] += 1
                per_file[path]["todo"] += 1
            is_doc = text.startswith("##") or text.startswith("///") or text.startswith("/**")
            if not is_doc and CODEISH_RE.search(body) and ln != 10**9:
                if comment_block and ln == comment_block[-1] + 1:
                    comment_block.append(ln)
                else:
                    if len(comment_block) >= 3:
                        commented_code_blocks += 1
                        per_file[path]["commented_code"] += 1
                    comment_block = [ln]
            else:
                if len(comment_block) >= 3:
                    commented_code_blocks += 1
                    per_file[path]["commented_code"] += 1
                comment_block = []
        counts["commented_code"] += commented_code_blocks
        src = proj.snap.files.get(path, "")
        if role in ("code", "generated") and re.search(r"^\s*```", src, re.M) and not path.endswith((".md", ".py")):
            out.append(finding("smell.markdown-fence", "high", "prompt-archaeology",
                               "Markdown code fence inside %s" % path.rsplit("/", 1)[-1],
                               "A chat answer pasted whole: the fence lines break the file or sit in it as dead text.",
                               [(path, src[:src.index("```")].count("\n") + 1)]))
        if role == "code":
            prints = sum(1 for c in ff.calls if c["fn"] in ("print", "prints", "print_debug") or (c["fn"] == "log" and c["recv"] == "console"))
            per_file[path]["debug_prints"] = prints
            counts["debug_prints"] += prints
        name = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        if VERSIONED_RE.search(name) and role in ("code", "generated"):
            out.append(finding("smell.versioned-file", "medium", "prompt-archaeology",
                               "Version-suffixed file: %s" % path,
                               "`_old`, `_v2`, `copy` files are where two implementations of one thing live side by side; "
                               "an agent cannot tell which one is real.", [(path, 1)]))
            counts["versioned"] += 1

    # warnings silenced wholesale
    pf = next((f for f in proj.files.values() if f.lang == "godot-project"), None)
    silenced = list(pf.extra.get("warnings_off", [])) if pf else []
    inline_ignores = sum(f.annotations.get("warning_ignore", 0) + f.annotations.get("warning_ignore_start", 0) for f in proj.files.values())
    for path, src in proj.snap.files.items():
        if path.rsplit(".", 1)[-1] in CODE_EXT:
            inline_ignores += len(re.findall(r"eslint-disable|@ts-(ignore|nocheck|expect-error)|#\s*noqa|#\s*type:\s*ignore|#\s*pylint:\s*disable|"
                                             r"#\s*nosec|@SuppressWarnings|#pragma warning disable", src))
    if len(silenced) >= 3:
        ln = 0
        if pf:
            for i, l in enumerate(proj.snap.files[pf.path].split("\n"), 1):
                if l.startswith("gdscript/warnings/"):
                    ln = i
                    break
        out.append(finding("smell.warnings-silenced", "medium", "agent-risk",
                           "%d GDScript warnings are switched off project-wide" % len(silenced),
                           "%s. These are the analyzer's cheapest catches for exactly what agents get wrong: a shadowed "
                           "variable, an unused result, an integer division, a value inferred as Variant. Silenced "
                           "globally, no one sees them come back." % ", ".join(silenced),
                           [(pf.path, ln)] if pf else [], data={"warnings": silenced}))
    counts["silenced_warnings"] = len(silenced)
    counts["inline_ignores"] = inline_ignores

    # magic constants copied between files
    colors: Dict[str, Set[str]] = defaultdict(set)
    color_line: Dict[str, Dict[str, int]] = defaultdict(dict)
    for path, ff in proj.files.items():
        if proj.roles.get(path) not in ("code",):
            continue
        for s, ln in ff.strings:
            if re.fullmatch(r"#?[0-9a-fA-F]{6}([0-9a-fA-F]{2})?", s) and (s.startswith("#") or ff.lang == "gdscript"):
                colors[s.lower()].add(path)
                color_line[s.lower()].setdefault(path, ln)
    scattered = {c: fs for c, fs in colors.items() if len(fs) >= 3}
    counts["scattered_constants"] = len(scattered)
    if scattered:
        worst = sorted(scattered.items(), key=lambda kv: -len(kv[1]))[:6]
        ev = [(p, color_line[c][p]) for c, fs in worst[:3] for p in sorted(fs)[:2]]
        out.append(finding("smell.scattered-constants", "low", "duplication",
                           "%d colour values are typed out in 3 or more files each" % len(scattered),
                           "The same literal copied across files (%s). A restyle means finding every copy; an agent "
                           "changing one leaves the rest." % ", ".join("%s in %d" % (c, len(fs)) for c, fs in worst[:4]), ev))

    # docs: stale references and dead prompts
    doc_out = _doc_drift(proj)
    out.extend(doc_out["findings"])
    counts["stale_doc_refs"] = doc_out["stale"]
    counts["stale_doc_refs_agent"] = doc_out["stale_agent"]
    counts["dead_prompts"] = doc_out["dead_prompts"]
    counts["done_prompts"] = doc_out["done_prompts"]
    counts["agent_doc_lines"] = doc_out["agent_doc_lines"]
    counts["agent_docs"] = len(doc_out["agent_docs"])
    return {"findings": out, "counts": dict(counts), "per_file": {p: dict(c) for p, c in per_file.items()},
            "agent_docs": doc_out["agent_docs"], "prompts": doc_out["prompts"]}


def _doc_drift(proj: Project) -> dict:
    all_paths = set(proj.snap.all_paths)
    dirs = set()
    for p in all_paths:
        d = posixpath.dirname(p)
        while d and d not in dirs:
            dirs.add(d)
            d = posixpath.dirname(d)
    basenames = Counter(p.rsplit("/", 1)[-1] for p in all_paths)
    ignored = _gitignore_matcher(proj)
    findings: List[dict] = []
    stale_total = stale_agent = 0
    dead_prompts = done_prompts = 0
    agent_docs = []
    prompts = []
    member_cache = {}
    # a doc that an always-loaded agent file points at is read as authoritative too
    pointed = set()
    for path, df in proj.docs.items():
        if df.agent_weight >= 3:
            for span, _ in df.spans:
                if span.strip() in proj.docs:
                    pointed.add(span.strip())
    for path, df in sorted(proj.docs.items()):
        weight = max(df.agent_weight, 2 if path in pointed else 0)
        if df.agent_weight >= 3:
            agent_docs.append({"path": path, "lines": df.lines})
        stale = []
        seen_refs = set()
        starts = sorted((ln, s) for s, ln, _ in df.sentences)
        for span, ln in df.spans:
            # the sentences that could contain this span: those starting up to a few lines above it
            context = " ".join(s for sl, s in starts if ln - 8 <= sl <= ln and span in s)
            if REMOVAL_RE.search(context):
                continue
            s = span.strip().rstrip(".,;:")
            if s in seen_refs or " " in s or "*" in s or "<" in s or "://" in s or s.startswith(("-", "$", "~", "user:", "/")):
                continue
            if _PATHLIKE_RE.match(s) and "." in s.rsplit("/", 1)[-1]:
                if s.rsplit(".", 1)[-1].lower() not in _SOURCE_EXT:
                    continue
                rel = s.lstrip("./")
                base_doc = posixpath.dirname(path)
                candidates = {rel, posixpath.normpath(posixpath.join(base_doc, rel))}
                suffix_hit = "/" in rel and any(p.endswith("/" + rel) for p in all_paths)
                if not (candidates & all_paths) and not (candidates & dirs) and basenames.get(rel, 0) == 0 \
                        and not suffix_hit and not ignored(rel):
                    stale.append((s, ln, "file"))
                    seen_refs.add(s)
                continue
            if s.endswith("/") and _PATHLIKE_RE.match(s.rstrip("/")):
                if s.rstrip("/") not in dirs and not ignored(s):
                    stale.append((s, ln, "dir"))
                    seen_refs.add(s)
                continue
            m = _IDENT_REF_RE.match(s)
            if m and proj.godot_root is not None:
                head, member = m.group(1), m.group(2)
                target = proj.autoloads.get(head) or proj.classes.get(head)
                if target and proj.files.get(target) and proj.files[target].lang == "gdscript":
                    if target not in member_cache:
                        member_cache[target] = _declared_members(proj, target)
                    names, engine = member_cache[target]
                    if member not in names and not (engine and member in ENGINE_MEMBERS):
                        stale.append((s, ln, "member"))
                        seen_refs.add(s)
        if stale:
            stale_total += len(stale)
            if weight >= 2:
                stale_agent += len(stale)
            sev = "medium" if weight >= 2 else "low"
            lead = ("Agents load this file before they read any code, and take it at its word: " if weight >= 3 else
                    "An agent file points here as the current truth, and it cites " if path in pointed else
                    "The README is where an agent looks for how things work, and it cites " if weight == 2 else
                    "A doc in the repo cites ")
            findings.append(finding(
                "docs.stale-reference", sev, "prompt-archaeology",
                "%s cites %d %s that no longer exist%s" % (path, len(stale), "name" if len(stale) == 1 else "names", "s" if len(stale) == 1 else ""),
                lead + ", ".join("`%s`" % s for s, _, _ in stale[:8]) + (" (+%d more)" % (len(stale) - 8) if len(stale) > 8 else "") + ".",
                [(path, ln) for _, ln, _ in stale[:6]], data={"refs": [s for s, _, _ in stale], "weight": weight}))
        for p in df.prompts:
            status = docs_mod.classify_status(p["status"])
            prompts.append({"doc": path, "id": p["id"], "title": p["title"], "line": p["line"], "lines": p["lines"], "status": status,
                            "raw_status": p["status"]})
            if status == "dead":
                dead_prompts += 1
            elif status == "done":
                done_prompts += 1
    dead = [p for p in prompts if p["status"] == "dead"]
    if dead:
        by_doc = defaultdict(list)
        for p in dead:
            by_doc[p["doc"]].append(p)
        for doc, ps in by_doc.items():
            findings.append(finding(
                "docs.dead-prompts", "medium", "prompt-archaeology",
                "%d prompt%s marked do-not-run still sit in %s" % (len(ps), "" if len(ps) == 1 else "s", doc),
                "Prompt%s %s (%d lines) %s complete, runnable instructions for a design that was abandoned. The status "
                "lives in a table; an agent asked to “continue with the next prompt” reads the prompt, not the table." % (
                    "" if len(ps) == 1 else "s", ", ".join(p["id"] for p in ps), sum(p["lines"] for p in ps), "is" if len(ps) == 1 else "are"),
                [(doc, p["line"]) for p in ps], data={"prompts": [p["id"] for p in ps]}))
    return {"findings": findings, "stale": stale_total, "stale_agent": stale_agent, "dead_prompts": dead_prompts,
            "done_prompts": done_prompts, "agent_doc_lines": sum(d["lines"] for d in agent_docs), "agent_docs": agent_docs,
            "prompts": prompts}
