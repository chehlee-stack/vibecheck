"""Stringly-typed contracts: the coupling that no import shows.

In a vibe-coded project the dangerous dependencies are rarely `import`s. They are a screen
name passed to a router, an event name looked up in a handler table, the keys a handler reads
out of a payload dictionary, a save-file key, a signal, a meta key, a method a scene connects
to by name. Rename one side and nothing fails to compile; something just stops happening.

Every check here pairs *producers* of a string with its *consumers* and reports the pairs
that do not meet, with the lines on both sides.
"""
from __future__ import annotations

import difflib
import json
import math
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from .findings import finding
from .lang.godot_res import node_path
from .project import Project

_BUILTIN_METHODS = {
    "get", "set", "has", "erase", "append", "push_back", "emit", "connect", "call", "call_deferred", "new", "size",
    "keys", "values", "duplicate", "clear", "find", "insert", "remove_at", "pop_back", "sort", "sort_custom", "map",
    "filter", "reduce", "any", "all", "print", "str", "int", "float", "len", "range", "format", "join", "split",
}
_ENGINE_STRING_ARGS = {  # fn -> argument indexes that name engine vocabulary, not project contracts
    "tween_property": {1}, "tween_method": set(), "add_theme_color_override": {0}, "add_theme_constant_override": {0},
    "add_theme_font_size_override": {0}, "add_theme_stylebox_override": {0}, "add_theme_font_override": {0},
    "add_theme_icon_override": {0}, "get_theme_color": {0, 1}, "get_theme_constant": {0, 1}, "get_theme_font": {0, 1},
    "get_theme_font_size": {0, 1}, "get_theme_stylebox": {0, 1}, "get_theme_icon": {0, 1}, "has_theme_color": {0, 1},
    "set": {0}, "get": {0}, "has_method": {0}, "is_class": {0}, "print": {0}, "push_error": {0}, "push_warning": {0},
    "assert": {1}, "printerr": {0}, "prints": {0}, "print_debug": {0}, "format": {0},
}
_DICT_BUILTIN_BASES = re.compile(r"^(Time|OS|Engine|ProjectSettings|JSON|DisplayServer|Input|ClassDB|Performance|IP|RenderingServer)\.|"
                                 r"get_(property|method|signal)_list|get_datetime|get_date_dict|get_time_dict|environ|os\.|sys\.|json\.|response|headers|request|argv|kwargs")
_INPUT_FNS = {"is_action_pressed", "is_action_just_pressed", "is_action_just_released", "get_action_strength",
              "get_action_raw_strength", "is_action", "is_action_released", "action_press", "action_release"}
_GROUP_CONSUMERS = {"is_in_group", "get_nodes_in_group", "call_group", "get_first_node_in_group", "remove_from_group",
                    "set_group", "notify_group", "call_group_flags", "has_group"}
_IDENT_RE = re.compile(r"^[A-Za-z_][\w.:/-]{2,48}$")


@dataclass
class Table:
    file: str
    name: str
    line: int
    keys: Dict[str, int] = field(default_factory=dict)          # key -> line
    handlers: Dict[str, str] = field(default_factory=dict)      # key -> function name in the same file
    open: bool = False                                          # keys are also added at runtime
    access: str = "get"                                         # "get" drops unknown keys; "index" fails on them
    registered: Dict[str, Tuple[str, int]] = field(default_factory=dict)

    @property
    def id(self) -> str:
        return "%s::%s" % (self.file, self.name)


@dataclass
class Contracts:
    tables: Dict[str, Table] = field(default_factory=dict)
    routes: Dict[Tuple[str, str], List[Tuple[int, str]]] = field(default_factory=dict)  # (file, func) -> [(param idx, table id)]
    sends: List[dict] = field(default_factory=list)            # {table, key, file, line, fn, payload}
    findings: List[dict] = field(default_factory=list)
    string_edges: Dict[Tuple[str, str], dict] = field(default_factory=dict)   # (a, b) sorted -> {weight, strings}
    signals: List[dict] = field(default_factory=list)
    invariants: Dict[str, List[dict]] = field(default_factory=lambda: defaultdict(list))   # file -> [{kind, text, line}]


def analyze(proj: Project) -> Contracts:
    c = Contracts()
    code = {p: f for p, f in proj.files.items() if proj.roles.get(p) in ("code", "test", "tool", "generated")}
    _routing(proj, code, c)
    _dict_keys(proj, code, c)
    if proj.godot_root is not None:
        _members(proj, code, c)
        _signals(proj, code, c)
        _godot_strings(proj, code, c)
        _scenes(proj, c)
        _export_exclusions(proj, c)
    _broken_paths(proj, c)
    _shared_strings(proj, code, c)
    return c


# --------------------------------------------------------------------------- routing tables
def _routing(proj: Project, code: dict, c: Contracts) -> None:
    for path, ff in code.items():
        func_names = {f.name for f in ff.funcs}
        for d in ff.dicts:
            name = d["assigned"]
            if not name or name == "return" or "[" in name or "(" in name:
                continue
            if d["func"] not in (None, "_init", "_ready", "__init__", "setup") or len(d["keys"]) < 2:
                continue
            name = name.split(".")[-1]
            t = c.tables.get("%s::%s" % (path, name)) or Table(path, name, d["line"])
            for key, ln, value in d["keys"]:
                t.keys.setdefault(key, ln)
                if value and value in func_names:
                    t.handlers[key] = value
            c.tables[t.id] = t
        for m in ff.matches:
            # a `match` with no matching branch falls through: unknown keys are ignored, not fatal
            t = Table(path, "%s:match" % m["func"], m["line"], access="get")
            t.keys = {k: ln for k, ln in m["keys"]}
            c.tables[t.id] = t

    # routers: functions that look their parameter up in a table of the same file
    by_name: Dict[str, List[Tuple[str, str]]] = defaultdict(list)   # func name -> [(file, func)]
    defined_in: Dict[str, Set[str]] = defaultdict(set)
    for path, ff in code.items():
        for f in ff.funcs:
            defined_in[f.name].add(path)
    for path, ff in code.items():
        tables_here = {t.name: t for t in c.tables.values() if t.file == path}
        if not tables_here:
            continue
        for f in ff.funcs:
            routes = []
            assert_lines = {call["line"] for call in ff.calls if call["func"] == f.name and call["fn"] == "assert"}
            has_guarded = {(call["recv"] or "").split(".")[-1] for call in ff.calls
                           if call["func"] == f.name and call["fn"] == "has" and call["line"] not in assert_lines}
            for s in ff.name_subs:
                base = s["base"].split(".")[-1]
                if s["func"] == f.name and base in tables_here and s["key"] in f.params:
                    t = tables_here[base]
                    if s.get("mode") in ("write", "rw"):
                        t.open = True
                        continue
                    if base not in has_guarded:   # `if T.has(k): T[k]` is a lookup with a fallback
                        t.access = "index"
                    routes.append((f.params.index(s["key"]), t.id))
            for call in ff.calls:
                recv = (call["recv"] or "").split(".")[-1]
                if call["func"] == f.name and call["fn"] in ("get", "has", "get_or_add") and recv in tables_here and \
                        call["args"] and call["args"][0]["k"] == "name" and call["args"][0]["v"] in f.params:
                    routes.append((f.params.index(call["args"][0]["v"]), tables_here[recv].id))
            mt = "%s::%s:match" % (path, f.name)
            if mt in c.tables:
                m = next(m for m in ff.matches if m["func"] == f.name)
                if m["param"] in f.params:
                    routes.append((f.params.index(m["param"]), mt))
            if routes:
                c.routes[(path, f.name)] = sorted(set(routes))
                by_name[f.name].append((path, f.name))

    parent_of: Dict[str, str] = {}
    for path, ff in code.items():
        if ff.extends:
            target = proj.res_to_path(ff.extends) if ff.extends.startswith("res://") else proj.classes.get(ff.extends)
            if target and target in code:
                parent_of[path] = target

    def ancestors(p: str) -> Set[str]:
        out, cur, guard = set(), p, 0
        while cur in parent_of and guard < 12:
            cur = parent_of[cur]
            out.add(cur)
            guard += 1
        return out

    def related(a: str, b: str) -> bool:
        return a == b or b in ancestors(a) or a in ancestors(b)

    def call_targets(call: dict, path: str) -> List[Tuple[str, str]]:
        """Which routers a call can reach: same file for bare calls, the named global's file for
        `Game.x()`, and any same-named router for calls through an untyped variable."""
        targets = by_name.get(call["fn"], [])
        recv = call["recv"]
        if recv in (None, "self"):
            return [t for t in targets if t[0] == path]
        head = recv.split(".")[0].split("[")[0]
        known = proj.autoloads.get(head) or proj.classes.get(head)
        if known:
            return [t for t in targets if t[0] == known]
        # Through an untyped variable the name must be unambiguous. A base class that declares the
        # same method is not ambiguity — that is the override the router exists to be.
        router_files = {t[0] for t in targets}
        others = defined_in.get(call["fn"], set()) - router_files
        if any(not any(related(o, rf) for rf in router_files) for o in others):
            return []
        return targets

    # forwarders: functions that pass their parameter straight on to a router (or forwarder)
    for _ in range(3):
        added = False
        for path, ff in code.items():
            for f in ff.funcs:
                for call in ff.calls:
                    if call["func"] != f.name or call["fn"] in _BUILTIN_METHODS or call["fn"] not in by_name:
                        continue
                    targets = call_targets(call, path)
                    for j, a in enumerate(call["args"]):
                        if a["k"] != "name" or a["v"] not in f.params:
                            continue
                        for tgt in targets:
                            for idx, tid in c.routes.get(tgt, []):
                                if idx == j:
                                    entry = (f.params.index(a["v"]), tid)
                                    lst = c.routes.setdefault((path, f.name), [])
                                    if entry not in lst:
                                        lst.append(entry)
                                        added = True
                                        if (path, f.name) not in by_name[f.name]:
                                            by_name[f.name].append((path, f.name))
        if not added:
            break

    # runtime registrations: register("kind", fn) style calls that write into an open table
    writers: Dict[str, List[Tuple[int, str]]] = defaultdict(list)  # func name -> [(param idx, table id)]
    for path, ff in code.items():
        tables_here = {t.name: t for t in c.tables.values() if t.file == path}
        for f in ff.funcs:
            for s in ff.name_subs:
                base = s["base"].split(".")[-1]
                if s["func"] == f.name and base in tables_here and s.get("mode") == "write" and s["key"] in f.params:
                    writers[f.name].append((f.params.index(s["key"]), tables_here[base].id))

    # sends: every call with a literal where a route expects a key
    for path, ff in proj.files.items():
        if path not in code:
            continue
        for call in ff.calls:
            fn = call["fn"]
            if fn in writers:
                for idx, tid in writers[fn]:
                    if idx < len(call["args"]) and call["args"][idx]["k"] == "str":
                        c.tables[tid].registered.setdefault(call["args"][idx]["v"], (path, call["line"]))
            if fn not in by_name or fn in _BUILTIN_METHODS:
                continue
            for tgt in call_targets(call, path):
                for idx, tid in c.routes.get(tgt, []):
                    if idx >= len(call["args"]) or call["args"][idx]["k"] != "str":
                        continue
                    if (path, call["func"]) == tgt:
                        continue
                    payload = None
                    for a in call["args"]:
                        if a["k"] == "dict" and a["v"] is not None:
                            payload = [k for k, _, _ in ff.dicts[a["v"]]["keys"]]
                    c.sends.append({"table": tid, "key": call["args"][idx]["v"], "file": path, "line": call["line"],
                                    "fn": fn, "payload": payload})

    literal_sites: Dict[str, Set[str]] = defaultdict(set)
    for path, ff in code.items():
        for s, _ in ff.strings:
            literal_sites[s].add(path)

    seen_send = set()
    for s in c.sends:
        key = (s["table"], s["key"], s["file"], s["line"])
        if key in seen_send:
            continue
        seen_send.add(key)
    by_table: Dict[str, List[dict]] = defaultdict(list)
    for s in c.sends:
        by_table[s["table"]].append(s)

    for tid, t in c.tables.items():
        sends = by_table.get(tid, [])
        if not sends:
            continue
        router_fns = sorted({fn for (p, fn), rs in c.routes.items() for _, x in rs if x == tid})
        sent_keys = defaultdict(list)
        for s in sends:
            sent_keys[s["key"]].append(s)
        router_src = []
        for (p, fn), rs in c.routes.items():
            if p == t.file and any(x == tid for _, x in rs):
                rf = next((f for f in proj.files[p].funcs if f.name == fn), None)
                if rf:
                    router_src.extend(proj.snap.files.get(p, "").split("\n")[rf.line - 1:rf.end])
        router_text = "\n".join(router_src)
        for key, sites in sorted(sent_keys.items()):
            if key in t.keys or key in t.registered or not key.strip():
                continue   # "" is a sentinel for "none", handled before the lookup
            if re.search(r'[!=]=\s*"%s"|"%s"\s*[!=]=' % (re.escape(key), re.escape(key)), router_text):
                continue   # the router compares against this key explicitly: a handled special case
            suggestion = difflib.get_close_matches(key, list(t.keys), n=1, cutoff=0.75)
            ev = [(x["file"], x["line"]) for x in sites[:4]] + [(t.file, t.line)]
            if t.access == "index" and not t.open:
                c.findings.append(finding(
                    "contract.unknown-key", "high", "agent-risk",
                    "`%s` is sent to %s but %s has no such key" % (key, "/".join(router_fns), t.name),
                    "Every call below passes '%s', and %s looks it up by index, which fails at runtime.%s" % (
                        key, t.name, (" Did you mean '%s'?" % suggestion[0]) if suggestion else ""),
                    ev, data={"table": tid, "key": key}))
            elif t.handlers:
                c.findings.append(finding(
                    "contract.dropped-key", "low", "agent-risk",
                    "`%s` is sent %d time%s but %s has no handler for it" % (key, len(sites), "" if len(sites) == 1 else "s", t.name),
                    "%s looks the key up with a default, so the send silently does nothing. Harmless if intended; "
                    "an agent adding behaviour for '%s' must know it is unwired, not missing." % (t.name, key),
                    ev, data={"table": tid, "key": key}))
        sent = set(sent_keys)
        dead = [k for k in t.keys if k not in sent and len(literal_sites.get(k, set()) - {t.file}) == 0
                and k not in t.registered]
        if dead and len(sends) >= 3:
            c.findings.append(finding(
                "contract.unsent-key", "info", "prompt-archaeology",
                "%d key%s of %s %s never sent anywhere" % (len(dead), "" if len(dead) == 1 else "s", t.name, "is" if len(dead) == 1 else "are"),
                "No literal anywhere in the code names %s. Dead entries, or sent through a variable VibeCheck cannot follow." % (
                    ", ".join("'%s'" % k for k in dead[:6])),
                [(t.file, t.keys[k]) for k in dead[:6]], data={"table": tid, "keys": dead}))

        # payload shape: what each handler reads hard from its info dictionary, vs what each send carries
        if t.handlers:
            ff = proj.files[t.file]
            funcs = {f.name: f for f in ff.funcs}
            src_lines = proj.snap.files.get(t.file, "").split("\n")
            for key, handler in t.handlers.items():
                f = funcs.get(handler)
                if not f or not f.params:
                    continue
                param = f.params[0]
                hard = {}
                for sub in ff.subs:
                    if sub["func"] == handler and sub["base"] == param and sub["mode"] == "read":
                        hard.setdefault(sub["keys"][0], sub["line"])
                returns = [ln for ln in range(f.line + 1, f.end + 1)
                           if ln - 1 < len(src_lines) and re.search(r"\breturn\b", src_lines[ln - 1].split("#")[0])]
                for site in sent_keys.get(key, []):
                    if site["payload"] is None:
                        continue
                    missing = [k for k in hard if k not in site["payload"]]
                    if not missing:
                        continue
                    from_test = proj.roles.get(site["file"]) == "test"
                    behind_return = any(r < min(hard[k] for k in missing) for r in returns)
                    if from_test:
                        sev, why = "low", ("A test sends a partial payload and relies on %s leaving early; if that early return "
                                           "ever moves, the test crashes instead of failing." % handler)
                    elif behind_return:
                        sev, why = "medium", ("%s reads these keys without a default after an early return; this send only "
                                              "survives while that return is taken." % handler)
                    else:
                        sev, why = "high", ("%s indexes %s[...] for these keys without a default; this send's dictionary "
                                            "does not carry them, so the handler fails the first time this path runs." % (handler, param))
                    c.findings.append(finding(
                        "contract.payload-missing", sev, "agent-risk",
                        "`%s` sent without %s, which %s reads unguarded" % (key, ", ".join("'%s'" % k for k in missing), handler),
                        why, [(site["file"], site["line"])] + [(t.file, hard[k]) for k in missing[:3]],
                        data={"table": tid, "key": key, "missing": missing}))
                for k, ln in hard.items():
                    c.invariants[t.file].append({"kind": "payload", "line": ln,
                                                 "text": "handler %s requires info['%s'] for event '%s'" % (handler, k, key)})
        for k, ln in t.keys.items():
            if sent_keys.get(k):
                c.invariants[t.file].append({"kind": "routing", "line": ln, "text": "'%s' is a key of %s, sent from %d site%s" % (
                    k, t.name, len(sent_keys[k]), "" if len(sent_keys[k]) == 1 else "s")})


# --------------------------------------------------------------------------- dictionary keys
def _dict_keys(proj: Project, code: dict, c: Contracts) -> None:
    produced: Dict[str, List[Tuple[str, int]]] = defaultdict(list)
    for path, ff in proj.files.items():
        for d in ff.dicts:
            for k, ln, _ in d["keys"]:
                produced[k].append((path, ln))
        for s in ff.subs:
            if s["mode"] in ("write",):
                produced[s["keys"][-1]].append((path, s["line"]))
        for call in ff.calls:
            if call["fn"] in ("set_meta", "get_or_add", "merge") and call["args"] and call["args"][0]["k"] == "str":
                produced[call["args"][0]["v"]].append((path, call["line"]))
    for path, src in proj.snap.files.items():
        if path.endswith(".json") and len(src) < 400_000:
            try:
                data = json.loads(src)
            except ValueError:
                continue
            stack = [data]
            while stack:
                x = stack.pop()
                if isinstance(x, dict):
                    for k, v in x.items():
                        produced[str(k)].append((path, 0))
                        stack.append(v)
                elif isinstance(x, list):
                    stack.extend(x)

    # Keys a format string could have produced ("recall@%d" -> recall@5) are not missing keys.
    composed = []
    for path, ff in proj.files.items():
        for s, _ in ff.strings:
            if len(s) > 3 and re.search(r"%[sdfx]|\{\}|\{\w+\}", s):
                # re.escape leaves % alone and escapes braces, so both forms are matched here
                composed.append(re.sub(r"%[sdfxi]|\\\{\\\}|\\\{\w+\\\}", ".+", re.escape(s)))
    composed_re = re.compile("^(?:%s)$" % "|".join(composed)) if composed else None

    dangling: Dict[str, List[Tuple[str, int, str, bool]]] = defaultdict(list)
    for path, ff in code.items():
        if proj.roles.get(path) == "tool":
            continue
        # dictionaries filled through a variable key (`cache[name] = ...`) can hold any key
        dynamic_bases = {s["base"].split(".")[-1] for s in ff.name_subs if s.get("mode") == "write"}
        literal_lines = defaultdict(set)
        for sv, ln in ff.strings:
            literal_lines[sv].add(ln)
        guarded = {(call["func"], (call["recv"] or ""), call["args"][0]["v"]) for call in ff.calls
                   if call["fn"] in ("has", "get") and call["args"] and call["args"][0]["k"] == "str"}
        for s in ff.subs:
            if _DICT_BUILTIN_BASES.search(s["base"]) or s["mode"] == "write":
                continue
            # `d[k] += 1` fails on a missing key in GDScript; in Python it is how a Counter is used
            if s["mode"] == "rw" and ff.lang != "gdscript":
                continue
            if s["base"].split(".")[-1].split("[")[0] in dynamic_bases:
                continue
            for k in s["keys"]:
                if k in produced or (composed_re and composed_re.match(k)):
                    continue
                if literal_lines.get(k, set()) - {s["line"]} and not any((s["func"], s["base"], k) == g for g in guarded):
                    continue  # the name exists as a value elsewhere in the file: probably a dynamic key
                is_guarded = (s["func"], s["base"], k) in guarded
                dangling[k].append((path, s["line"], s["base"], is_guarded))
    for k, sites in sorted(dangling.items(), key=lambda kv: -len(kv[1])):
        if all(g for *_, g in sites):
            c.findings.append(finding(
                "contract.dead-branch", "low", "prompt-archaeology",
                "Nothing ever produces the key `%s`, so the branches that check for it never run" % k,
                "Each read is behind a has()/get() check on a key no dictionary, assignment or data file in the repo "
                "creates: leftover plumbing from an earlier design, or a feature that was never wired.",
                [(p, ln) for p, ln, _, _ in sites[:5]], data={"key": k}))
            continue
        c.findings.append(finding(
            "contract.key-never-written", "medium", "agent-risk",
            "`[\"%s\"]` is read %d time%s but no code or data file ever writes that key" % (k, len(sites), "" if len(sites) == 1 else "s"),
            "No dictionary literal, assignment or JSON file in the repo produces '%s'. Either the data comes from outside "
            "the repo, or this read fails at runtime (a renamed key is the usual story)." % k,
            [(p, ln) for p, ln, _, _ in sites[:5]], data={"key": k}))


# --------------------------------------------------------------------------- members of globals that are gone
# Members every Godot script inherits from the engine classes it can extend. A reference to one of
# these through a project global is not a dangling reference, whatever the script declares.
ENGINE_MEMBERS = frozenset("""
new free get set call callv call_deferred connect disconnect is_connected emit_signal has_method has_signal get_class
is_class get_script set_script get_meta set_meta has_meta remove_meta get_meta_list get_property_list get_method_list
get_signal_list notification to_string get_instance_id is_queued_for_deletion set_deferred get_indexed set_indexed tr
tr_n set_block_signals is_blocking_signals add_user_signal has_user_signal get_signal_connection_list cancel_free
reference unreference get_reference_count init_ref resource_path resource_name duplicate emit_changed changed get_rid
setup_local_to_scene take_over_path add_child remove_child get_child get_children get_child_count get_node
get_node_or_null has_node get_parent find_child find_children find_parent get_tree is_inside_tree is_node_ready
queue_free reparent move_child add_sibling get_index get_path get_path_to owner name process_mode set_process
set_physics_process set_process_input set_process_unhandled_input set_process_unhandled_key_input is_processing
is_physics_processing get_process_delta_time get_physics_process_delta_time add_to_group remove_from_group
is_in_group get_groups create_tween get_viewport get_window print_tree print_tree_pretty propagate_call
propagate_notification request_ready set_owner get_owner replace_by ready tree_entered tree_exiting tree_exited
child_entered_tree child_exiting_tree renamed scene_file_path rpc rpc_id rpc_config multiplayer process_priority
can_process update_configuration_warnings visible modulate self_modulate show_behind_parent top_level light_mask
texture_filter texture_repeat material use_parent_material z_index z_as_relative y_sort_enabled show hide
is_visible_in_tree queue_redraw draw_line draw_rect draw_circle draw_texture draw_texture_rect draw_string
draw_polygon draw_colored_polygon draw_polyline draw_arc draw_set_transform draw_style_box get_canvas_item
get_global_mouse_position get_local_mouse_position get_global_transform get_transform get_viewport_rect
get_canvas_transform make_input_local get_world_2d force_update_transform draw visibility_changed hidden
item_rect_changed is_visible set_visible size position global_position rotation scale pivot_offset
custom_minimum_size size_flags_horizontal size_flags_vertical anchor_left anchor_right anchor_top anchor_bottom
offset_left offset_right offset_top offset_bottom mouse_filter focus_mode tooltip_text theme set_anchors_preset
set_anchors_and_offsets_preset set_offsets_preset get_rect get_global_rect grab_focus release_focus has_focus
accept_event get_minimum_size get_combined_minimum_size set_size set_position set_global_position get_size
get_position gui_input mouse_entered mouse_exited focus_entered focus_exited resized minimum_size_changed
clip_contents grow_horizontal grow_vertical update_minimum_size get_parent_control rotation_degrees skew transform
global_rotation global_scale global_transform look_at translate rotate apply_scale to_local to_global get_angle_to
instantiate set_process_mode script process physics_process input unhandled_input
""".split())


def _declared_members(proj: Project, path: str, depth: int = 0) -> Tuple[Set[str], bool]:
    """Names a script declares or inherits from project scripts, and whether an engine class is above it."""
    ff = proj.files.get(path)
    if not ff or depth > 8:
        return set(), True
    names = {s["name"] for s in ff.symbols} | {f.name for f in ff.funcs}
    engine = False
    if ff.extends:
        parent = proj.res_to_path(ff.extends) if ff.extends.startswith("res://") else proj.classes.get(ff.extends)
        if parent:
            more, engine = _declared_members(proj, parent, depth + 1)
            names |= more
        else:
            engine = True
    else:
        engine = True   # no extends means RefCounted
    return names, engine


def _members(proj: Project, code: dict, c: Contracts) -> None:
    cache: Dict[str, Tuple[Set[str], bool]] = {}
    missing: Dict[Tuple[str, str], List[Tuple[str, int]]] = defaultdict(list)
    for path, ff in code.items():
        if ff.lang != "gdscript":
            continue
        for name, member, line in ff.refs:
            if not member or name == ff.class_name:
                continue
            target = proj.autoloads.get(name) or proj.classes.get(name)
            if not target or target not in proj.files or proj.files[target].lang != "gdscript":
                continue
            if target not in cache:
                cache[target] = _declared_members(proj, target)
            names, engine = cache[target]
            if member in names or (engine and member in ENGINE_MEMBERS):
                continue
            missing[(name, member)].append((path, line))
    for (name, member), sites in sorted(missing.items()):
        target = proj.autoloads.get(name) or proj.classes.get(name)
        c.findings.append(finding(
            "godot.missing-member", "high", "agent-risk",
            "`%s.%s` is used but %s declares no `%s`" % (name, member, target.rsplit("/", 1)[-1], member),
            "Moved, renamed or deleted on one side only — the usual trace of a refactor that missed a caller. "
            "GDScript rejects the calling script at parse time, so everything that loads it stops working.",
            sites[:5] + [(target, 1)], data={"global": name, "member": member}))


# --------------------------------------------------------------------------- Godot signals, meta, groups, actions
def _signals(proj: Project, code: dict, c: Contracts) -> None:
    declared: Dict[str, List[Tuple[str, int]]] = defaultdict(list)
    for path, ff in code.items():
        for name, ln in ff.signals:
            declared[name].append((path, ln))
    emitted: Dict[str, List[Tuple[str, int]]] = defaultdict(list)
    connected: Dict[str, List[Tuple[str, int, Optional[str]]]] = defaultdict(list)
    for path, ff in code.items():
        for name, ln in ff.emits:
            emitted[name].append((path, ln))
        for recv, name, ln in ff.connects:
            connected[name].append((path, ln, recv))
    for name, decls in declared.items():
        for path, ln in decls:
            owner = proj.files[path]
            conns = [x for x in connected.get(name, []) if x[0] != path or True]
            c.signals.append({"name": name, "file": path, "line": ln, "emits": len(emitted.get(name, [])),
                              "connects": len(conns), "listeners": sorted({x[0] for x in conns if x[0] != path})})
            if not emitted.get(name):
                sev = "medium" if conns else "low"
                c.findings.append(finding(
                    "godot.signal-never-emitted", sev, "prompt-archaeology",
                    "Signal `%s` is declared%s but never emitted" % (name, " and connected" if conns else ""),
                    ("Listeners in %s wait for an event that never fires." % ", ".join(sorted({x[0].rsplit('/', 1)[-1] for x in conns})))
                    if conns else "Declared API nobody fires: dead, or emitted by string where VibeCheck cannot see it.",
                    [(path, ln)] + [(x[0], x[1]) for x in conns[:3]]))
            listeners = sorted({x[0] for x in conns if x[0] != path})
            if listeners:
                c.invariants[path].append({"kind": "signal", "line": ln, "text": "signal %s has listeners in %d other file%s" % (
                    name, len(listeners), "" if len(listeners) == 1 else "s")})
    for name, conns in connected.items():
        for path, ln, recv in conns:
            head = (recv or "").split(".")[0]
            target = proj.autoloads.get(head) or proj.classes.get(head)
            if target and target in proj.files and name not in {s for s, _ in proj.files[target].signals} \
                    and not proj.files[target].extends:
                c.findings.append(finding(
                    "godot.connect-unknown-signal", "high", "agent-risk",
                    "`%s.%s.connect` names a signal %s does not declare" % (head, name, target.rsplit("/", 1)[-1]),
                    "Renamed or removed on one side only.", [(path, ln)]))


def _godot_strings(proj: Project, code: dict, c: Contracts) -> None:
    pf = next((f for f in proj.files.values() if f.lang == "godot-project"), None)
    actions = set(pf.extra.get("actions", {})) if pf else set()
    meta_set: Dict[str, List[Tuple[str, int]]] = defaultdict(list)
    meta_get: Dict[str, List[Tuple[str, int, str]]] = defaultdict(list)
    groups_add: Dict[str, List[Tuple[str, int]]] = defaultdict(list)
    groups_use: Dict[str, List[Tuple[str, int]]] = defaultdict(list)
    for path, ff in code.items():
        for call in ff.calls:
            a0 = call["args"][0] if call["args"] else None
            if not a0 or a0["k"] != "str":
                continue
            fn, v = call["fn"], a0["v"]
            if fn == "set_meta":
                meta_set[v].append((path, call["line"]))
            elif fn in ("get_meta", "has_meta", "remove_meta"):
                meta_get[v].append((path, call["line"], fn))
            elif fn == "add_to_group":
                groups_add[v].append((path, call["line"]))
            elif fn in _GROUP_CONSUMERS:
                groups_use[v].append((path, call["line"]))
            elif fn in _INPUT_FNS and not v.startswith("ui_") and v not in actions:
                c.findings.append(finding(
                    "godot.unknown-input-action", "high", "agent-risk",
                    "Input action `%s` is not defined in project.godot" % v,
                    "The engine reports an unknown action and the input never fires.", [(path, call["line"])]))
    for f in proj.files.values():
        if f.lang == "godot-scene":
            for node in f.extra.get("nodes", []):
                pass
    for key, sites in meta_get.items():
        if key not in meta_set:
            hard = [s for s in sites if s[2] == "get_meta"]
            c.findings.append(finding(
                "godot.meta-never-set", "medium" if hard else "low", "agent-risk",
                "Meta key `%s` is read but never set" % key,
                "get_meta without a default errors when the key is missing; has_meta is always false." if hard else
                "has_meta checks for a key nothing sets.", [(p, ln) for p, ln, _ in sites[:4]]))
    for key, sites in groups_use.items():
        if key not in groups_add:
            c.findings.append(finding(
                "godot.group-never-joined", "medium", "agent-risk",
                "Group `%s` is queried but nothing joins it in code" % key,
                "Unless a scene file adds nodes to it, every query returns nothing.", sites[:4]))
    shared_meta = {k: v for k, v in meta_set.items() if len({p for p, _ in v} | {p for p, _, _ in meta_get.get(k, [])}) >= 2}
    for k, sites in shared_meta.items():
        for p, ln in sites:
            c.invariants[p].append({"kind": "meta", "line": ln, "text": "meta key '%s' is read in other files" % k})


def _scenes(proj: Project, c: Contracts) -> None:
    for path, ff in proj.files.items():
        if ff.lang != "godot-scene":
            continue
        nodes = ff.extra.get("nodes", [])
        by_path = {node_path(nodes, n): n for n in nodes}
        for conn in ff.extra.get("connections", []):
            node = by_path.get(conn["to"] or ".")
            script = proj.res_to_path(node["script"]) if node and node.get("script") else None
            if not script or script not in proj.files:
                continue
            methods = _methods_with_inheritance(proj, script)
            if conn["method"] not in methods:
                c.findings.append(finding(
                    "godot.scene-connection-missing-method", "high", "agent-risk",
                    "%s connects `%s` to `%s()`, which %s does not define" % (path.rsplit("/", 1)[-1], conn["signal"], conn["method"], script.rsplit("/", 1)[-1]),
                    "A method renamed in the script but not in the scene: the signal fires into nothing and logs an error.",
                    [(path, conn["line"])]))
            else:
                f = next((f for f in proj.files[script].funcs if f.name == conn["method"]), None)
                c.invariants[script].append({"kind": "scene", "line": f.line if f else 0,
                                             "text": "%s() is called by name from %s" % (conn["method"], path)})


def _methods_with_inheritance(proj: Project, path: str, depth: int = 0) -> Set[str]:
    ff = proj.files.get(path)
    if not ff or depth > 8:
        return set()
    names = {f.name for f in ff.funcs}
    if ff.extends:
        parent = proj.res_to_path(ff.extends) if ff.extends.startswith("res://") else proj.classes.get(ff.extends)
        if parent:
            names |= _methods_with_inheritance(proj, parent, depth + 1)
        else:
            names.add("*")  # an engine class: its methods are unknown to us, so do not flag
    return names if "*" not in names else names | {"*"}


def _export_exclusions(proj: Project, c: Contracts) -> None:
    if not proj.export_excludes:
        return
    for (src, dst), e in proj.edges.items():
        if e.kind not in ("preload", "extends", "class", "scene-script", "ext_resource", "autoload"):
            continue
        if proj.export_excluded(dst) and not proj.export_excluded(src) and proj.roles.get(src) in ("code", "generated", "resource", "config"):
            c.findings.append(finding(
                "godot.export-excluded-dependency", "high", "agent-risk",
                "%s depends at compile time on %s, which the export leaves out" % (src.rsplit("/", 1)[-1], dst),
                "export_presets.cfg excludes it, so on a device the dependent script fails to compile: the build "
                "boots to a blank screen with no error. Use load() in a dev-only branch instead.",
                [(src, ln) for ln in e.lines[:3]], data={"kind": e.kind}))


def _broken_paths(proj: Project, c: Contracts) -> None:
    seen = set()
    for b in proj.broken_paths:
        key = (b["file"], b["path"])
        if key in seen:
            continue
        seen.add(key)
        c.findings.append(finding(
            "path.missing-resource", "high" if b["how"] in ("preload", "extends", "ext_resource", "autoload") else "medium",
            "agent-risk", "`%s` does not exist" % b["path"],
            "Referenced by %s (%s) but no such file is in the repository at this commit." % (b["file"].rsplit("/", 1)[-1], b["how"]),
            [(b["file"], b["line"])]))


# --------------------------------------------------------------------------- shared strings as coupling
def _shared_strings(proj: Project, code: dict, c: Contracts) -> None:
    sites: Dict[str, Dict[str, int]] = defaultdict(dict)   # string -> file -> first line
    for path, ff in code.items():
        if proj.roles.get(path) == "tool":
            continue
        skip_lines = set()
        for call in ff.calls:
            idxs = _ENGINE_STRING_ARGS.get(call["fn"])
            if idxs:
                skip_lines.add((call["line"], tuple(a["v"] for i, a in enumerate(call["args"]) if i in idxs and a["k"] == "str")))
        skip = {(ln, v) for ln, vs in skip_lines for v in vs}
        for s, ln in ff.strings:
            if not _IDENT_RE.match(s) or s.startswith("res://") or (ln, s) in skip or s.isupper() and len(s) <= 3:
                continue
            sites[s].setdefault(path, ln)
    n = max(1, len(code))
    cap = max(4, int(0.2 * n))
    for s, files in sites.items():
        df = len(files)
        if df < 2 or df > cap:
            continue
        idf = math.log(n / df) / math.log(n) if n > 1 else 1.0
        fl = sorted(files)
        for i in range(len(fl)):
            for j in range(i + 1, len(fl)):
                key = (fl[i], fl[j])
                e = c.string_edges.setdefault(key, {"weight": 0.0, "strings": []})
                e["weight"] += idf / (df - 1)
                if len(e["strings"]) < 8:
                    e["strings"].append(s)
