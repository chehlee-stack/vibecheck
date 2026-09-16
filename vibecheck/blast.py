"""Blast radius: what a change request will probably break, before an agent touches the code.

1. Seeds. The request is matched (BM25) against what each file is about: its path, its global
   name, its symbols, its doc comments and its strings, with a small thesaurus so "inventory"
   finds a wardrobe and "multiplayer" finds the network.
2. Constraints. Sentences in the docs that use the request's words *and* forbid something are
   surfaced before anything else; a request that contradicts one is flagged, not planned.
3. Impact. Personalized PageRank from the seeds over the coupling graph, walking edges the way
   breakage travels: from a file to everything that depends on it, to what shares its strings,
   and to what history says changes with it. Each impacted file keeps the path that reached it.
4. The handoff pack: a brief for a coding agent with the files, the invariants, the rules the
   docs already state, the checks to run, and when to stop.
"""
from __future__ import annotations

import heapq
import math
import re
from collections import Counter, defaultdict
from typing import Dict, List, Optional, Tuple

STOP = set("""
a an the to of for and or in on at by with from into onto as is are be been it its this that these those there here
we you i my our your their them they he she her his all any some more most less new old better best good make made
add adding added replace replacing replaced remove removing removed delete deleting change changing changed update
updating updated rework redo rewrite rewriting implement implementing implementation support supporting build create
creating allow allowing let lets use using want need needs should would could can will just also so then than
system systems feature features thing things stuff way ways instead via per each every into out up down over under
refactor refactoring rename renaming fix fixing fixed bug bugs issue issues improve improving proper properly
""".split())
INTENTS = [
    ("remove", r"\b(remove|delete|drop|kill|rip out|get rid of|deprecate)\b"),
    ("replace", r"\b(replace|rewrite|redo|rework|swap|migrate|overhaul|rebuild|port)\b"),
    ("rename", r"\b(rename|move)\b"),
    ("fix", r"\b(fix|bug|broken|crash|repair)\b"),
    ("refactor", r"\b(refactor|split|extract|clean ?up|decouple|simplify)\b"),
    ("add", r"\b(add|new|create|implement|support|introduce|enable|allow)\b"),
]
CONCEPTS = {
    "multiplayer": "network networking online internet server client socket peer lobby sync rpc remote websocket enet versus coop player players matchmaking",
    "online": "network internet server client socket http request sync cloud",
    "network": "internet online server client socket http request multiplayer",
    "inventory": "item items bag owned own collection wardrobe equip equipment loot stash grant slot slots piece pieces",
    "item": "items inventory owned piece pieces grant loot",
    "save": "save load persist storage file json schema migration progress profile userdata restore",
    "cloud": "sync network online server account backup upload",
    "shop": "store purchase buy price cost coin coins currency economy sell booster",
    "economy": "price cost coin coins payout currency reward shop",
    "reward": "payout coin coins prize chest box drop reward",
    "sound": "sfx audio music volume synth tone",
    "audio": "sfx sound music volume synth",
    "music": "sfx sound audio track volume",
    "ui": "screen menu button dialog hud panel layout theme widget",
    "menu": "screen title button dialog navigation router",
    "screen": "ui menu router scene navigation",
    "level": "board stage generation difficulty band map track",
    "difficulty": "band level tune tuning generation balance hard easy",
    "tutorial": "onboarding hint help explain tip guide",
    "achievement": "medal medals trophy badge stat stats tally",
    "medal": "achievement trophy badge stat stats tally",
    "daily": "calendar streak event season tournament day week",
    "event": "daily calendar season tournament",
    "login": "auth account user session password token oauth",
    "account": "login auth user profile session",
    "payment": "purchase iap billing subscription ads monetization store price",
    "ads": "advert advertisement monetization reward payment",
    "analytics": "tracking telemetry metrics event log",
    "notification": "push reminder alert notify",
    "localization": "translation language locale i18n translate text",
    "settings": "options preference preferences config toggle setting",
    "animation": "tween vfx effect particle shake popup motion",
    "effect": "vfx particle tween popup shake animation fx",
    "physics": "collision body velocity gravity rigid",
    "character": "player brownie avatar sprite rig pose",
    "outfit": "wardrobe piece pieces worn wear costume dress house set",
    "powerup": "booster power hammer shuffle rocket bomb rainbow special",
    "test": "smoke rules tests assert check",
}
CONSTRAINT_RE = re.compile(r"\b(never|must not|mustn't|must|do not|don't|not negotiable|only|always|stays?|forbidden|cannot|can't|"
                           r"nothing|no\s+\w+|without|not)\b|=\s*false|pillar", re.I)
NEGATION_RE = re.compile(r"\b(no|never|nothing|not|without|don't|do not|must not|cannot|can't|stays? false|forbidden)\b|=\s*false", re.I)
_SPLIT_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")


def stem(w: str) -> str:
    w = w.lower()
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 5 and w.endswith(("sses", "shes", "ches", "xes")):
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us", "is")):
        return w[:-1]
    if len(w) > 5 and w.endswith("ing"):
        return w[:-3]
    if len(w) > 4 and w.endswith("ed"):
        return w[:-2]
    return w


def words(text: str) -> List[str]:
    out = []
    for chunk in re.split(r"[^A-Za-z0-9]+", text):
        for part in _SPLIT_RE.findall(chunk):
            p = part.lower()
            if len(p) >= 2 and not p.isdigit():
                out.append(p)
    return out


def parse_request(text: str) -> dict:
    low = text.lower()
    intent = next((name for name, pat in INTENTS if re.search(pat, low)), "change")
    raw = [w for w in words(text) if w not in STOP]
    terms: Dict[str, float] = {}
    concepts = []
    for w in raw:
        terms[stem(w)] = max(terms.get(stem(w), 0.0), 1.0)
        syn = CONCEPTS.get(w) or CONCEPTS.get(stem(w))
        if syn:
            concepts.append(w)
            for s in syn.split():
                terms.setdefault(stem(s), 0.45)
    return {"text": text, "intent": intent, "raw": [stem(w) for w in raw], "terms": terms, "concepts": concepts}


def file_terms(scan, path: str) -> Dict[str, float]:
    ff = scan.proj.files[path]
    tf: Counter = Counter()
    for w in words(path.rsplit(".", 1)[0]):
        tf[stem(w)] += 3
    if ff.class_name:
        for w in words(ff.class_name):
            tf[stem(w)] += 3
    for s in ff.symbols:
        for w in words(s["name"]):
            tf[stem(w)] += 1.0
        for w in words(s.get("doc", ""))[:60]:
            tf[stem(w)] += 0.6
    for w in words(ff.doc)[:120]:
        tf[stem(w)] += 1.0
    for sv, _ in ff.strings[:400]:
        if 2 < len(sv) < 40 and not sv.startswith("res://"):
            for w in words(sv):
                tf[stem(w)] += 0.4
    for _, c in ff.comments[:300]:
        for w in words(c)[:30]:
            tf[stem(w)] += 0.25
    kept = sorted(((k, v) for k, v in tf.items() if k not in STOP and len(k) > 1), key=lambda kv: (-kv[1], kv[0]))[:220]
    return {k: round(min(v, 30.0), 2) for k, v in kept}


def _index(scan) -> Dict[str, Dict[str, float]]:
    cache = getattr(scan, "_blast_index", None)
    if cache is None:
        cache = {p: file_terms(scan, p) for p in scan.graph.nodes
                 if scan.proj.roles.get(p) in ("code", "generated", "resource", "test")}
        scan._blast_index = cache
    return cache


def bm25(index: Dict[str, Dict[str, float]], query: Dict[str, float], k1: float = 1.2, b: float = 0.6) -> Dict[str, Tuple[float, List[str]]]:
    n = len(index) or 1
    df = Counter()
    lens = {}
    for p, tf in index.items():
        lens[p] = sum(tf.values())
        for t in tf:
            df[t] += 1
    avg = sum(lens.values()) / n if lens else 1.0
    out = {}
    for p, tf in index.items():
        s = 0.0
        hits = []
        for t, qw in query.items():
            f = tf.get(t)
            if not f:
                continue
            idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
            s += qw * idf * f * (k1 + 1) / (f + k1 * (1 - b + b * lens[p] / avg))
            hits.append(t)
        if s > 0:
            out[p] = (s, hits)
    return out


_NEG_WORDS = {"no", "never", "nothing", "not", "without", "cannot", "forbidden", "none", "nobody", "neither", "nor"}
_GENERIC_TERMS = {"player", "game", "level", "screen", "item", "one", "file", "data", "state", "event", "test", "day", "time"}


def _conflicts(sentence: str, q: dict) -> bool:
    """A request to add something the docs forbid: a negation within a few words of the request's concept.
    Related words (the thesaurus) count only when the concept itself is new to the code: "no purchases"
    does not forbid a booster in a shop that already sells boosters, but "no network" does forbid multiplayer."""
    if q["intent"] not in ("add", "change"):
        return False
    toks = [w.lower() for w in re.findall(r"[A-Za-z]+", sentence)]
    stems = [stem(t) for t in toks]
    negs = [i for i, t in enumerate(toks) if t in _NEG_WORDS]
    negs += [i for i, t in enumerate(toks) if t == "false" and i > 0]
    floor = 0.45 if q.get("novel") else 1.0
    hits = [i for i, s in enumerate(stems) if q["terms"].get(s, 0) >= floor and s not in _GENERIC_TERMS]
    return any(0 < j - i <= 6 or (toks[i] == "false" and 0 < i - j <= 4) for i in negs for j in hits)


def _table_rules(scan, names: set) -> List[dict]:
    """Rows of agent-doc tables that name the files in play, each with its table's header for context."""
    out = []
    for path, df in scan.proj.docs.items():
        if df.agent_weight < 3:
            continue
        header, last = None, -10
        for cells, ln in df.table_rows:
            if ln - last > 2:
                header = cells
            last = ln
            if cells is header:
                continue
            text = " | ".join(cells)
            hit = [n for n in names if n and re.search(r"\b%s\b" % re.escape(n.rsplit(".", 1)[0]), text)]
            if hit:
                pairs = ["%s %s" % (h.rstrip(". "), c) for h, c in zip(header or [], cells) if c]
                out.append({"doc": path, "line": ln, "text": "; ".join(pairs) if header else text, "files": hit[:3], "weight": df.agent_weight})
    return out


def _constraints(scan, q: dict, seeds: List[str]) -> List[dict]:
    seed_names = set()
    for s in seeds:
        seed_names.add(s.rsplit("/", 1)[-1])
        cn = scan.proj.files[s].class_name
        if cn:
            seed_names.add(cn)
    found = []
    for path, df in scan.proj.docs.items():
        if df.agent_weight < 1 or df.is_prompt_file:
            continue
        for sent, ln, heading in df.sentences:
            if len(sent) > 420:
                continue
            ws = {stem(w) for w in words(sent)}
            overlap = sum(q["terms"][t] for t in ws if t in q["terms"])
            mentions = [n for n in seed_names if n in sent]
            if overlap <= 0 and not mentions:
                continue
            if not CONSTRAINT_RE.search(sent):
                continue
            score = (overlap + 0.8 * len(mentions)) * (1 + df.agent_weight)
            conflict = _conflicts(sent, q) and not mentions
            found.append({"doc": path, "line": ln, "text": sent, "heading": heading, "score": round(score, 2),
                          "conflict": conflict, "weight": df.agent_weight})
    found.sort(key=lambda c: (-c["conflict"], -c["score"]))
    out, seen = [], set()
    for c in found:
        key = c["text"][:80]
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
        if len(out) >= 8:
            break
    # configuration that encodes a constraint: an export that forbids the network
    net_terms = {"network", "internet", "online", "server", "multiplayer", "socket", "http", "sync", "cloud", "analytics", "login", "account"}
    if net_terms & set(q["terms"]) and q["intent"] != "remove":
        for f in scan.proj.files.values():
            if f.lang == "godot-export":
                for preset in f.extra.get("presets", []):
                    if str(preset.get("internet", "")).lower() == "false":
                        out.insert(0, {"doc": f.path, "line": 0, "text": "Export preset \"%s\" ships with permissions/internet=false." % preset["name"],
                                       "heading": "export_presets.cfg", "score": 99, "conflict": True, "weight": 3})
                        break
    return out


def _impact_weights(scan, seeds: List[str], q: dict) -> Dict[str, List[Tuple[str, float, dict]]]:
    """The graph's impact edges, with edges out of a hub seed scaled by relevance: a screen that only reads
    `Game.coins()` is not in the blast radius of an inventory rewrite just because Game.gd is."""
    nbrs = scan.graph.impact_neighbors()
    out: Dict[str, List[Tuple[str, float, dict]]] = {}
    hubs = _hubs(scan, seeds)
    relevant = {s: _relevant_members(scan, s, q) for s in hubs}
    passive = {p for p, r in scan.proj.roles.items() if r in ("test", "tool")}   # they are reported, not propagated through
    for u, lst in nbrs.items():
        lst = [(v, w, e) for v, w, e in lst if v not in passive]
        if u not in hubs:
            out[u] = lst
            continue
        scaled = []
        for v, w, e in lst:
            se = scan.proj.edges.get((v, u))
            static = e["kinds"].get("static", 0.0)
            if se is not None and static:
                members = se.members
                share = (len(members & relevant[u]) / len(members)) if members else 0.0
                factor = 0.2 + 0.8 * min(1.0, 2 * share)
                w = static * factor + (e["w"] - static)
            scaled.append((v, w, e))
        out[u] = scaled
    return out


def _hubs(scan, seeds: List[str]) -> set:
    return {s for s in seeds if scan.metrics.get(s, {}).get("fan_in", 0) >= 6}


def _relevant_members(scan, path: str, q: dict) -> set:
    """Members of a file whose name or doc comment speaks to the request."""
    rel = set()
    for sym in scan.proj.files[path].symbols:
        text_terms = {stem(w) for w in words(sym["name"])} | {stem(w) for w in words(sym.get("doc", ""))}
        if any(q["terms"].get(t, 0) >= 0.45 for t in text_terms):
            rel.add(sym["name"])
    return rel


def _ppr(nbrs: Dict[str, List[Tuple[str, float, dict]]], seeds: List[str], alpha: float = 0.55, iters: int = 40) -> Dict[str, float]:
    p0 = {s: 1.0 / len(seeds) for s in seeds}
    r = dict(p0)
    for _ in range(iters):
        nxt: Dict[str, float] = defaultdict(float)
        for u, mass in r.items():
            out = nbrs.get(u, [])
            tot = sum(w for _, w, _ in out)
            if tot <= 0:
                continue
            for v, w, _ in out:
                nxt[v] += alpha * mass * w / max(tot, 1.0)
        for s in seeds:
            nxt[s] += (1 - alpha) * p0[s]
        r = nxt
    return r


def _paths(nbrs: Dict[str, List[Tuple[str, float, dict]]], seeds: List[str]) -> Dict[str, List[Tuple[str, str, dict]]]:
    """Cheapest evidence path from any seed to each file, walking impact edges."""
    dist = {s: 0.0 for s in seeds}
    prev: Dict[str, Tuple[str, dict]] = {}
    heap = [(0.0, s) for s in seeds]
    while heap:
        d, u = heapq.heappop(heap)
        if d > dist.get(u, 1e9):
            continue
        for v, w, e in nbrs.get(u, []):
            nd = d + 0.35 - math.log(min(1.0, max(w, 1e-3)))
            if nd < dist.get(v, 1e9):
                dist[v] = nd
                prev[v] = (u, e)
                heapq.heappush(heap, (nd, v))
    out = {}
    for v in prev:
        chain = []
        cur = v
        while cur in prev and len(chain) < 6:
            u, e = prev[cur]
            chain.append((u, cur, e))
            cur = u
        out[v] = list(reversed(chain))
    return out


def run(scan, request: str, seeds: Optional[List[str]] = None, max_impacted: int = 12) -> dict:
    q = parse_request(request)
    index = _index(scan)
    ranking = bm25({p: t for p, t in index.items() if scan.proj.roles.get(p) in ("code", "generated", "resource")}, q["terms"])
    ordered = sorted(ranking.items(), key=lambda kv: -kv[1][0])
    known_terms = set()
    for tf in index.values():
        known_terms.update(tf)
    missing = [t for t in q["raw"] if t not in known_terms]
    top = ordered[0][1][0] if ordered else 0.0
    if seeds:
        seed_list = [s for s in seeds if s in scan.proj.files]
    else:
        seed_list = [p for p, (s, _) in ordered if s >= 0.4 * top][:5]
    novel = bool(q["raw"]) and len(missing) == len(q["raw"])
    q["novel"] = novel
    constraints = _constraints(scan, q, seed_list)
    risk_by = {r["path"]: r for r in scan.ranking}
    if not seed_list:
        return {"request": request, "intent": q["intent"], "terms": q["terms"], "missing_terms": missing, "novel": True,
                "seeds": [], "impacted": [], "context": [], "tests": [], "constraints": constraints,
                "invariants": [], "rules": [], "stop": [], "summary": "Nothing in the code matches this request.", "pack": ""}

    nbrs = _impact_weights(scan, seed_list, q)
    r = _ppr(nbrs, seed_list)
    paths = _paths(nbrs, seed_list)
    tests = {p for p, role in scan.proj.roles.items() if role == "test"}
    seed_set = set(seed_list)
    hubs = _hubs(scan, seed_list)
    relevant = {s: _relevant_members(scan, s, q) for s in seed_list}
    candidates = [(p, v) for p, v in r.items() if p not in seed_set and scan.proj.roles.get(p) in ("code", "generated", "resource")]
    candidates.sort(key=lambda kv: -kv[1])
    best = candidates[0][1] if candidates else 0.0
    impacted = []
    for p, v in candidates[:max_impacted]:
        if v < 0.12 * best:
            break
        chain = paths.get(p, [])
        kinds = set(chain[-1][2]["kinds"]) if chain else set()
        # a direct dependency counts as "breaks" only if it uses what the request is about
        direct_edges = []
        for s in seed_list:
            ge = scan.graph.edges.get((p, s))
            if not ge or not ({"static", "contract"} & set(ge["kinds"])):
                continue
            se = scan.proj.edges.get((p, s))
            used = se.members if se else set()
            if s in hubs and "contract" not in ge["kinds"] and not (used & relevant[s]):
                continue
            # a registry that only preloads the file (a router's screen table) breaks on rename or removal, not on an edit
            if se is not None and se.kind in ("preload", "load", "ext_resource", "scene-script") and not used \
                    and q["intent"] not in ("remove", "rename", "replace"):
                continue
            direct_edges.append((s, ge, sorted(used & relevant[s]) if s in hubs else sorted(used)))
        if scan.proj.roles.get(p) == "resource" and not direct_edges:
            continue
        if direct_edges:
            kind = "breaks"
            s, ge, used = max(direct_edges, key=lambda d: d[1]["w"])
            because = ["uses %s of %s" % (", ".join(used[:5]), s.rsplit("/", 1)[-1])] if used else ge["why"][:2]
            why = [{"from": s, "to": p, "because": because, "kinds": sorted(ge["kinds"])}]
        else:
            kind = "ripples" if kinds & {"static", "contract"} else "changes-with" if "cochange" in kinds else "shares-strings"
            why = [{"from": a, "to": b, "because": e["why"][:2], "kinds": sorted(e["kinds"])} for a, b, e in chain]
        rr = risk_by.get(p, {})
        impacted.append({"path": p, "score": round(v, 5), "kind": kind, "tier": rr.get("tier"), "risk": rr.get("score"), "why": why})
    order = {"breaks": 0, "ripples": 1, "changes-with": 2, "shares-strings": 3}
    impacted.sort(key=lambda i: (order[i["kind"]], -i["score"]))

    context = []
    for (src, dst), e in scan.graph.edges.items():
        if src in seed_set and dst not in seed_set and "static" in e["kinds"] and scan.proj.roles.get(dst) in ("code", "generated"):
            context.append({"path": dst, "w": e["w"], "because": e["why"][:2], "from": src})
    context.sort(key=lambda c: -c["w"])
    seen_ctx, ctx = set(), []
    for c in context:
        if c["path"] in seen_ctx or any(i["path"] == c["path"] for i in impacted):
            continue
        seen_ctx.add(c["path"])
        ctx.append(c)
    ctx = ctx[:6]

    area = seed_set | {i["path"] for i in impacted}
    test_hits = Counter()
    for (src, dst), e in scan.graph.edges.items():
        if src in tests and dst in area and ({"static", "contract"} & set(e["kinds"])):
            test_hits[src] += 2 if dst in seed_set else 1
    test_list = [{"path": p, "hits": n} for p, n in test_hits.most_common(6)]

    invariants = []
    for p in seed_list + [i["path"] for i in impacted[:6]]:
        m = scan.metrics.get(p, {})
        if m.get("generated_by"):
            invariants.append({"file": p, "line": 1, "text": "Generated by `%s`: change the generator and re-run it; never hand-edit." % m["generated_by"]})
        if m.get("persists"):
            invariants.append({"file": p, "line": 1, "text": "Writes persistent data: existing saves must still load after the change."})
    for p in seed_list:
        users = defaultdict(set)
        for e in scan.proj.in_edges(p):
            if scan.proj.roles.get(e.src) in ("code", "generated"):
                for mname in e.members:
                    users[mname].add(e.src)
        name = scan.proj.files[p].class_name or next((n for n, f in scan.proj.autoloads.items() if f == p), None) or p.rsplit("/", 1)[-1]
        if p in hubs:   # a hub's whole API is not this request's business: keep the members that speak to it
            users = {m: fs for m, fs in users.items() if m in relevant[p]} or dict(sorted(users.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:2])
        for mname, fs in sorted(users.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:6]:
            if len(fs) >= 2:
                invariants.append({"file": p, "line": _decl_line(scan, p, mname),
                                   "text": "`%s.%s` is used by %d files (%s): keep its signature and meaning." % (
                                       name, mname, len(fs), ", ".join(sorted(f.rsplit("/", 1)[-1] for f in fs)[:4]))})
    for p in seed_list:
        for inv in scan.contracts.invariants.get(p, [])[:5]:
            if inv["kind"] in ("routing", "signal", "scene", "meta"):
                invariants.append({"file": p, "line": inv["line"], "text": inv["text"]})
    if scan.proj.export_excludes:
        invariants.append({"file": "export_presets.cfg", "line": 0,
                           "text": "The export leaves out %s: shipped code must not preload or name anything there." % ", ".join(scan.proj.export_excludes[:4])})
    seen_inv, inv_out = set(), []
    for inv in invariants:
        if inv["text"] in seen_inv:
            continue
        seen_inv.add(inv["text"])
        inv_out.append(inv)
    inv_out = inv_out[:16]

    rules = []
    names = {p.rsplit("/", 1)[-1] for p in area} | {scan.proj.files[p].class_name for p in area if scan.proj.files[p].class_name}
    for path, df in scan.proj.docs.items():
        if df.agent_weight < 2 or df.is_prompt_file:
            continue
        for sent, ln, heading in df.sentences:
            hit = [n for n in names if n and re.search(r"\b%s\b" % re.escape(n.rsplit(".", 1)[0]), sent)]
            if hit and CONSTRAINT_RE.search(sent) and len(sent) < 400:
                rules.append({"doc": path, "line": ln, "text": sent, "files": hit[:3], "weight": df.agent_weight})
    rules = _table_rules(scan, names) + rules
    seed_names = {p.rsplit("/", 1)[-1].rsplit(".", 1)[0] for p in seed_list} | {scan.proj.files[p].class_name for p in seed_list if scan.proj.files[p].class_name}

    def rule_score(r: dict) -> float:
        on_seed = sum(1 for n in seed_names if n and re.search(r"\b%s\b" % re.escape(n), r["text"]))
        return 3 * on_seed + len(r["files"]) + r["weight"] - 0.004 * len(r["text"])
    rules.sort(key=lambda r: -rule_score(r))
    rules = rules[:8]

    stop = [{"path": p, "reasons": risk_by[p]["reasons"][:3], "score": risk_by[p]["score"]}
            for p in sorted(area, key=lambda x: -risk_by.get(x, {}).get("score", 0))
            if risk_by.get(p, {}).get("tier") in ("do-not-touch", "generated")][:6]

    hist = scan.history
    budget_lines = int(min(800, max(200, 2 * hist.median_code_lines))) if hist and hist.commits >= 5 else 400
    budget_files = len(seed_list) + sum(1 for i in impacted if i["kind"] == "breaks") + 2
    summary = _summary(scan, q, seed_list, impacted, constraints, novel, missing, stop)
    result = {"request": request, "intent": q["intent"], "terms": {k: round(v, 2) for k, v in q["terms"].items()},
              "missing_terms": missing, "novel": novel,
              "seeds": [{"path": p, "score": round(ranking[p][0], 2), "matched": ranking[p][1][:6], "tier": risk_by.get(p, {}).get("tier"),
                         "risk": risk_by.get(p, {}).get("score")} for p in seed_list if p in ranking] +
                       [{"path": p, "score": 0, "matched": [], "tier": risk_by.get(p, {}).get("tier"), "risk": risk_by.get(p, {}).get("score")}
                        for p in seed_list if p not in ranking],
              "impacted": impacted, "context": ctx, "tests": test_list, "constraints": constraints, "invariants": inv_out,
              "rules": rules, "stop": stop, "budget": {"files": budget_files, "lines": budget_lines}, "summary": summary}
    result["pack"] = handoff_pack(scan, result)
    return result


def _decl_line(scan, path: str, name: str) -> int:
    for s in scan.proj.files[path].symbols:
        if s["name"] == name:
            return s["line"]
    return 1


def _summary(scan, q, seeds, impacted, constraints, novel, missing, stop) -> str:
    parts = []
    conflicts = [c for c in constraints if c["conflict"]]
    if conflicts:
        parts.append("This request runs into %d documented constraint%s; settle %s before any code is written." % (
            len(conflicts), "" if len(conflicts) == 1 else "s", "it" if len(conflicts) == 1 else "them"))
    if novel:
        parts.append("Nothing in the code is called %s yet, so the radius is measured from the closest existing ideas." % " or ".join("“%s”" % m for m in missing[:2]))
    breaks = [i for i in impacted if i["kind"] == "breaks"]
    parts.append("The change lands in %s and reaches %d more file%s: %d depend%s on the seeds directly, %d through another file, %d only by history or shared strings." % (
        ", ".join(p.rsplit("/", 1)[-1] for p in seeds[:3]), len(impacted), "" if len(impacted) == 1 else "s",
        len(breaks), "s" if len(breaks) == 1 else "", sum(1 for i in impacted if i["kind"] == "ripples"),
        sum(1 for i in impacted if i["kind"] in ("changes-with", "shares-strings"))))
    if stop:
        parts.append("%d of them %s on the do-not-touch list." % (len(stop), "is" if len(stop) == 1 else "are"))
    if any(scan.metrics.get(i["path"], {}).get("persists") for i in impacted) or any(scan.metrics.get(s, {}).get("persists") for s in seeds):
        parts.append("Saved data is inside the radius.")
    return " ".join(parts)


def handoff_pack(scan, b: dict) -> str:
    repo = scan.snap.root.resolve().name
    L = []
    L.append("# Task: %s" % b["request"])
    L.append("")
    L.append("You are working in `%s` at commit `%s`. VibeCheck wrote this brief from a static and historical analysis of the "
             "repository. Every claim names a file; check a claim against the code before you rely on it." % (repo, scan.snap.short))
    L.append("")
    conflicts = [c for c in b["constraints"] if c["conflict"]]
    if conflicts:
        L.append("## 0. Stop: the request contradicts the project's own rules")
        L.append("")
        for c in conflicts[:4]:
            loc = "%s:%d" % (c["doc"], c["line"]) if c["line"] else c["doc"]
            L.append("> %s  \n> — `%s`" % (c["text"], loc))
            L.append("")
        L.append("Do not write code until the person who asked confirms how this request squares with the lines above.")
        L.append("")
    if b["novel"]:
        L.append("> Nothing in the codebase is named %s yet. The files below are where it would connect, found through related ideas." % (
            " or ".join("“%s”" % m for m in b["missing_terms"][:3])))
        L.append("")
    L.append("## 1. Where the change goes")
    L.append("")
    for s in b["seeds"]:
        tier = " — **%s**" % s["tier"] if s["tier"] in ("do-not-touch", "generated") else ""
        L.append("- `%s`%s  (matched: %s)" % (s["path"], tier, ", ".join(s["matched"][:5]) or "given"))
    L.append("")
    L.append("## 2. What will probably break")
    L.append("")
    labels = {"breaks": "depends on it directly", "ripples": "reached through another file", "changes-with": "changes with it in history",
              "shares-strings": "shares string keys with it"}
    for i in b["impacted"]:
        why = ""
        if i["kind"] == "breaks" and i["why"]:
            why = " — " + "; ".join(i["why"][0]["because"])
        elif i["why"]:
            hops = [i["why"][0]["from"]] + [w["to"] for w in i["why"]]
            why = " — via %s: %s" % (" → ".join(h.rsplit("/", 1)[-1] for h in hops), "; ".join(i["why"][-1]["because"]))
        tier = " **[%s]**" % i["tier"] if i["tier"] == "do-not-touch" else ""
        L.append("- `%s` (%s)%s%s" % (i["path"], labels[i["kind"]], why, tier))
    if not b["impacted"]:
        L.append("- Nothing beyond the files above: they have no dependents VibeCheck can see.")
    L.append("")
    if b["context"]:
        L.append("## 3. Read before editing (change only if the task requires it)")
        L.append("")
        for c in b["context"]:
            L.append("- `%s` — %s" % (c["path"], "; ".join(c["because"])))
        L.append("")
    if b["invariants"]:
        L.append("## 4. Invariants to preserve")
        L.append("")
        for inv in b["invariants"]:
            loc = "%s:%d" % (inv["file"], inv["line"]) if inv.get("line") else inv["file"]
            L.append("- %s (`%s`)" % (inv["text"], loc))
        L.append("")
    nonconflict = [c for c in b["constraints"] if not c["conflict"]]
    if b["rules"] or nonconflict:
        L.append("## 5. Rules the docs already state")
        L.append("")
        seen = set()
        for r in (b["rules"] + nonconflict)[:10]:
            if r["text"] in seen:
                continue
            seen.add(r["text"])
            L.append("> %s  \n> — `%s:%d`" % (r["text"], r["doc"], r["line"]))
            L.append("")
    if b["stop"]:
        L.append("## 6. Do not modify without stopping to ask")
        L.append("")
        for s in b["stop"]:
            L.append("- `%s` — %s" % (s["path"], "; ".join(s["reasons"])))
        L.append("")
    L.append("## 7. Verify")
    L.append("")
    cmds = [c for c in _commands(scan)][:2]
    for c in cmds:
        L.append("From `%s` (%s):" % (c["doc"], c["heading"]))
        L.append("")
        L.append("```bash")
        L.append(c["text"])
        L.append("```")
        L.append("")
    if b["tests"]:
        L.append("Tests that exercise this area: %s." % ", ".join("`%s`" % t["path"] for t in b["tests"]))
    else:
        L.append("No test file references these files. Before changing behaviour, write a test that pins what the code does now.")
    L.append("")
    L.append("## 8. Stop or roll back when")
    L.append("")
    allowed = [s["path"] for s in b["seeds"]] + [i["path"] for i in b["impacted"] if i["kind"] == "breaks"]
    L.append("- The change needs an edit outside these files, beyond updating a call site: %s." % ", ".join("`%s`" % p.rsplit("/", 1)[-1] for p in allowed[:10]))
    L.append("- The diff grows past about %d changed lines or %d files. That is the change getting away from the request." % (b["budget"]["lines"], b["budget"]["files"]))
    if b["stop"]:
        L.append("- A file from section 6 needs more than a call-site change.")
    if any(inv["text"].startswith("Writes persistent data") for inv in b["invariants"]):
        L.append("- Saved data would change shape. Stop and ask; if approved, follow the project's migration rule.")
    L.append("- A string key, signal or payload field from section 4 is renamed in some of its sites but not all of them.")
    L.append("- A check in section 7 fails and the only way to make it pass is to change what the test expects.")
    L.append("")
    L.append("## 9. Report back with")
    L.append("")
    L.append("- The files you changed, and why each one needed to change.")
    L.append("- Any invariant from section 4 you had to touch, and how its other sites were updated.")
    L.append("- The commands you ran from section 7 and their results.")
    L.append("- Anything listed in section 2 that you did not check.")
    L.append("")
    return "\n".join(L)


_TEST_CMD_RE = re.compile(r"\b(test|tests|smoke|rules|check|lint|pytest|jest|vitest|mocha|cargo test|go test|npm run|make)\b|--\w*(test|smoke|rules)", re.I)


def _commands(scan) -> List[dict]:
    """Verification commands from the docs: blocks under a verify/test/done heading, test runners first."""
    out = []
    for path, df in scan.proj.docs.items():
        if df.is_prompt_file:
            continue
        first_in_heading = set()
        for blk in df.code_blocks:
            if blk["lang"] in ("bash", "sh", "shell", "zsh", "console", "") and re.search(r"verif|test|before|done|check|run", blk["heading"], re.I):
                text = blk["text"].strip()[:700]
                score = df.agent_weight + (3 if _TEST_CMD_RE.search(text) else 0) + (1 if blk["heading"] not in first_in_heading else 0) \
                    + min(2, text.count("\n"))
                first_in_heading.add(blk["heading"])
                out.append({"doc": path, "line": blk["line"], "heading": blk["heading"], "text": text, "weight": df.agent_weight, "score": score})
    out.sort(key=lambda c: -c["score"])
    deduped, seen = [], []
    for c in out:
        sig = set(re.findall(r"--[\w-]+", c["text"]))
        if any(sig and sig <= s for s in seen):
            continue
        seen.append(sig)
        deduped.append(c)
    return deduped
