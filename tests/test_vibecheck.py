"""Tests over a synthetic Godot project held in memory: no git, no fixtures on disk.

Each test states a fact a user would be told, and every detector that has ever produced a
false positive on a real repository has a case here for the shape that fooled it.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vibecheck import blast, boundaries, clones, contracts, project, scan, score  # noqa: E402
from vibecheck.lang import gdscript  # noqa: E402
from vibecheck.repo import Snapshot  # noqa: E402

PROJECT_GODOT = """
[application]
run/main_scene="res://scenes/Main.tscn"
[autoload]
Game="*res://scripts/autoload/Game.gd"
[debug]
gdscript/warnings/unused_variable=0
[input]
jump={"deadzone":0.5}
"""

EXPORT_PRESETS = """
[preset.0]
name="Android"
export_filter="all_resources"
exclude_filter="tools/*, docs/*"
[preset.0.options]
permissions/internet=false
"""

MAIN = '''extends Control
## Router: one screen at a time.

const SCREENS := {
\t"title": preload("res://scripts/screens/TitleScreen.gd"),
\t"shop": preload("res://scripts/screens/ShopScreen.gd"),
}

func _ready() -> void:
\tgo_to("title")
\tvar t := preload("res://tools/tune.gd").new()

func go_to(name: String) -> void:
\tassert(SCREENS.has(name), "no screen called '%s'" % name)
\tvar s: GDScript = SCREENS[name]
\tadd_child(s.new())
'''

GAME = '''extends Node
## Player state.
signal coins_changed(amount: int)
signal never_fired

var data: Dictionary = SaveFile.defaults()

func coins() -> int:
\treturn int(data["coins"])

func stat(id: String) -> int:
\treturn int(data["stats"].get(id, 0))

func add_coins(n: int) -> void:
\tdata["coins"] = max(0, coins() + n)
\tcoins_changed.emit(coins())
\tSaveFile.write(data)

func settle() -> void:
\tdata["stats"]["wins"] += 1
\tsettled.emit.call_deferred()
'''

SAVEFILE = '''class_name SaveFile
## The save schema, its version, and the atomic write.
const PATH := "user://save.json"

static func defaults() -> Dictionary:
\treturn {"coins": 0, "stats": {"wins": 0}, "outfit": Wardrobe.POSITIONS}

static func write(data: Dictionary) -> void:
\tvar f := FileAccess.open(PATH, FileAccess.WRITE)
\tf.store_string(JSON.stringify(data))
'''

WARDROBE = '''class_name Wardrobe
## The wardrobe's rules: which item is where. Plain data.
const POSITIONS := ["collar", "hat"]

static func texture(position: String) -> Texture2D:
\treturn Art.tex("res://assets/%s.png" % position)
'''

ART = '''class_name Art
## Cached painted-art loader.
static func tex(path: String) -> Texture2D:
\tUI.note()
\treturn load(path)
'''

UI = '''class_name UI
## Shared widgets.
static func note() -> void:
\tGame.add_coins(0)
'''

BOARD = '''class_name Board
extends Control
## Plays a move and reports what happened. Never names an effect.

var fx_sink: BoardFx

func _fx(kind: String, info: Dictionary) -> float:
\tif fx_sink == null:
\t\treturn 0.0
\treturn fx_sink.on(kind, info)

func play() -> void:
\t_fx("match", {"cells": [], "at": Vector2.ZERO})
\t_fx("ice_broken", {"at": Vector2.ZERO})
\t_fx("swap", {"a": 1, "b": 2})
\tGame.stat("wins")
'''

BOARDFX = '''class_name BoardFx
## The one door between a board and its presentation.
func on(_kind: String, _info: Dictionary) -> float:
\treturn 0.0
'''

FX = '''class_name Fx
extends BoardFx
## Maps board events to recipes.
var handlers := {}

func _init() -> void:
\thandlers = {"match": _match, "swap": _swap}

func on(kind: String, info: Dictionary) -> float:
\tvar h = handlers.get(kind)
\tif h == null:
\t\treturn 0.0
\treturn (h as Callable).call(info)

func _match(info: Dictionary) -> float:
\tvar c = info["cells"]
\tvar colour = info["color"]
\treturn 0.0

func _swap(_info: Dictionary) -> float:
\treturn 0.0
'''

SHOP = '''extends Screen
## Sells one booster.
func enter(main, _args) -> void:
\tmain.go_to("title")
\tGame.add_coins(-1)
\tvar n := Game.coins()
'''

TITLE = '''extends Screen
## Title card.
func enter(main, _args) -> void:
\tmain.go_to("shopp")
\tvar who := Game.player_name()
'''

TOOL = '''extends Node
## Dev tuner, excluded from the export.
func run() -> void:
\tprint("tuning")
'''

AGENTS = """# Rules for coding agents

## The invariants

1. **Domain models are plain data.** `Wardrobe` and `SaveFile` are pure functions. No `UI`,
   no `Game`. If a rule needs to be shown, the model reports it.
2. **The save schema is versioned.** Any change to `SaveFile.defaults()` needs a migration.

## Where a change goes

| If you are changing... | it lives in | it must not touch |
|---|---|---|
| the save schema | `scripts/SaveFile.gd` | anything else |

## Before claiming a change is done

```bash
godot --headless --path . -- --rules
```
"""

CLAUDE_MD = """# Notes

Nothing here talks to a network: `permissions/internet=false` in `export_presets.cfg` and it
stays false. The shop sells boosters for coins earned in play.

`scripts/screens/CollectionScreen.gd` holds the collection.
"""

FILES = {
    "project.godot": PROJECT_GODOT,
    "export_presets.cfg": EXPORT_PRESETS,
    "scripts/Main.gd": MAIN,
    "scripts/autoload/Game.gd": GAME,
    "scripts/SaveFile.gd": SAVEFILE,
    "scripts/Wardrobe.gd": WARDROBE,
    "scripts/ui/Art.gd": ART,
    "scripts/ui/UI.gd": UI,
    "scripts/Board.gd": BOARD,
    "scripts/vfx/BoardFx.gd": BOARDFX,
    "scripts/vfx/Fx.gd": FX,
    "scripts/screens/ShopScreen.gd": SHOP,
    "scripts/screens/TitleScreen.gd": TITLE,
    "tools/tune.gd": TOOL,
    "AGENTS.md": AGENTS,
    "CLAUDE.md": CLAUDE_MD,
}


def build(extra=None):
    files = dict(FILES)
    files.update(extra or {})
    snap = Snapshot(root=Path("/synthetic"), ref=None, files=files, all_paths=list(files) + ["assets/collar.png", "assets/hat.png"])
    proj = project.load(snap)
    con = contracts.analyze(proj)
    return snap, proj, con


def make_scan(snap, proj, con):
    """The pieces scan.run() assembles, without git."""
    from vibecheck import graph as graph_mod, risk as risk_mod, smells as smells_mod
    cl = clones.detect(proj)
    sm = smells_mod.analyze(proj)
    g = graph_mod.build(proj, con, None)
    metrics = risk_mod.file_metrics(proj, con, g, None, cl, sm)
    ranking = risk_mod.rank(proj, metrics, None)
    findings = list(con.findings) + list(sm["findings"]) + boundaries.check(proj)
    sc = score.compute(proj, con, None, metrics, cl, sm, findings, 0)
    return scan.Scan(snap=snap, proj=proj, history=None, commits=[], contracts=con, clones=cl, smells=sm, graph=g,
                     metrics=metrics, ranking=ranking, secret=[], findings=findings, score=sc)


def titles(findings, fid=None):
    return [f["title"] for f in findings if fid is None or f["id"] == fid]


class Tokenizer(unittest.TestCase):
    def test_strings_comments_and_paths(self):
        toks, comments, _ = gdscript.tokenize('var a := "x#y"  # note\nvar p := $Sprite/Anim\nvar q = a % 2\nvar u = %Unique\n')
        kinds = [(t[0], t[1]) for t in toks if t[0] != "nl"]
        self.assertIn(("str", "x#y"), kinds)                 # a # inside a string is not a comment
        self.assertEqual(comments[0][1], "# note")
        self.assertIn(("nodepath", "Sprite/Anim"), kinds)
        self.assertIn(("op", "%"), kinds)                     # modulo after an operand
        self.assertIn(("nodepath", "%Unique"), kinds)         # unique-name node where an operand starts

    def test_function_spans_and_doc(self):
        ff = gdscript.parse("x.gd", GAME, {"Game"})
        add = next(f for f in ff.funcs if f.name == "add_coins")
        self.assertEqual(add.length, 4)
        self.assertEqual(ff.doc, "Player state.")
        self.assertEqual({s[0] for s in ff.signals}, {"coins_changed", "never_fired"})

    def test_deferred_emit_counts_as_emitting(self):
        ff = gdscript.parse("x.gd", GAME, {"Game"})
        self.assertIn("settled", [name for name, _ in ff.emits])


class Contracts(unittest.TestCase):
    def setUp(self):
        self.snap, self.proj, self.con = build()

    def test_unknown_routing_key_is_high(self):
        found = [f for f in self.con.findings if f["id"] == "contract.unknown-key"]
        self.assertEqual(len(found), 1, titles(self.con.findings))
        self.assertIn("shopp", found[0]["title"])
        self.assertEqual(found[0]["severity"], "high")
        self.assertIn("Did you mean 'shop'?", found[0]["detail"])

    def test_known_routing_key_is_not_reported(self):
        self.assertNotIn("title", " ".join(titles(self.con.findings, "contract.unknown-key")))

    def test_event_with_no_handler_is_reported_softly(self):
        dropped = titles(self.con.findings, "contract.dropped-key")
        self.assertTrue(any("ice_broken" in t for t in dropped), dropped)
        self.assertTrue(all("swap" not in t for t in dropped))

    def test_payload_missing_key_the_handler_reads(self):
        found = [f for f in self.con.findings if f["id"] == "contract.payload-missing"]
        self.assertEqual(len(found), 1, titles(self.con.findings))
        self.assertIn("'color'", found[0]["title"])

    def test_member_that_no_longer_exists(self):
        found = titles(self.con.findings, "godot.missing-member")
        self.assertEqual(found, ["`Game.player_name` is used but Game.gd declares no `player_name`"])

    def test_signal_declared_but_never_emitted(self):
        self.assertTrue(any("never_fired" in t for t in titles(self.con.findings, "godot.signal-never-emitted")))

    def test_export_excluded_preload(self):
        found = titles(self.con.findings, "godot.export-excluded-dependency")
        self.assertEqual(len(found), 1)
        self.assertIn("tools/tune.gd", found[0])


class Boundaries(unittest.TestCase):
    def test_documented_rule_violated_through_a_helper(self):
        _, proj, _ = build()
        found = boundaries.check(proj)
        by_source = {f["data"]["source"]: f for f in found}
        self.assertIn("Wardrobe", by_source)                    # Wardrobe -> Art -> UI -> Game
        self.assertIn("UI", by_source["Wardrobe"]["data"]["reaches"])
        self.assertEqual(by_source["Wardrobe"]["severity"], "medium")   # not direct: it arrives through Art
        self.assertTrue(any("Art.gd" in hop for hop in by_source["Wardrobe"]["data"]["hops"]))


class Docs(unittest.TestCase):
    def test_stale_reference_in_an_agent_doc(self):
        from vibecheck import smells
        _, proj, _ = build()
        out = smells.analyze(proj)
        stale = [f for f in out["findings"] if f["id"] == "docs.stale-reference"]
        self.assertEqual(len(stale), 1)
        self.assertEqual(stale[0]["data"]["refs"], ["scripts/screens/CollectionScreen.gd"])


class Clones(unittest.TestCase):
    def test_cross_file_and_deterministic(self):
        body = "\n".join("\tvar v%d := %d + compute(%d, 'k%d')" % (i, i, i, i) for i in range(40))
        files = dict(FILES)
        files["scripts/A.gd"] = "class_name A\nfunc go() -> void:\n" + body + "\n"
        files["scripts/B.gd"] = "class_name B\nfunc other() -> void:\n" + body + "\n"
        snap = Snapshot(root=Path("/synthetic"), ref=None, files=files, all_paths=list(files))
        proj = project.load(snap)
        runs = [clones.detect(proj) for _ in range(2)]
        self.assertEqual(runs[0]["pairs"], runs[1]["pairs"])              # no hash-seed variance
        pair = [d for d in runs[0]["pairs"] if d["a"].endswith("A.gd") and d["b"].endswith("B.gd")]
        self.assertEqual(len(pair), 1, runs[0]["pairs"])


class Risk(unittest.TestCase):
    def test_the_file_that_writes_the_save_outranks_the_hubs(self):
        s = make_scan(*build())
        top = s.ranking[0]
        self.assertEqual(top["path"], "scripts/SaveFile.gd")            # persistence outranks centrality
        self.assertEqual(top["tier"], "do-not-touch")
        self.assertIn("writes persistent data", top["reasons"][0])

    def test_score_shape_and_thresholds(self):
        sc = make_scan(*build()).score
        self.assertGreater(sc["total"], 0)
        self.assertEqual(set(sc["categories"]), set(score.WEIGHTS))
        for cat in sc["categories"].values():                            # every component shows its thresholds
            for comp in cat["components"]:
                if comp["score"] is not None and not comp["value"].startswith("no "):
                    self.assertIn("range", comp, comp)   # every measured component prints what it is measured against


class Blast(unittest.TestCase):
    def setUp(self):
        self.scan = make_scan(*build())

    def test_words_and_stems(self):
        self.assertEqual(blast.words("addMultiplayer_toGame"), ["add", "multiplayer", "to", "game"])
        self.assertEqual(blast.stem("inventories"), "inventory")
        self.assertEqual(blast.stem("saves"), "save")

    def test_request_that_contradicts_a_documented_rule(self):
        b = blast.run(self.scan, "add multiplayer")
        conflicts = [c for c in b["constraints"] if c["conflict"]]
        self.assertTrue(conflicts, b["constraints"])
        self.assertTrue(any("internet" in c["text"] for c in conflicts))
        self.assertTrue(b["novel"])
        self.assertIn("multiplayer", b["missing_terms"])

    def test_request_within_the_rules_is_not_a_conflict(self):
        b = blast.run(self.scan, "add a booster to the shop")
        self.assertEqual([c for c in b["constraints"] if c["conflict"]], [])

    def test_blast_finds_the_save_and_writes_a_pack(self):
        b = blast.run(self.scan, "change the save schema")
        self.assertIn("scripts/SaveFile.gd", [s["path"] for s in b["seeds"]])
        self.assertIn("# Task: change the save schema", b["pack"])
        self.assertIn("## 8. Stop or roll back when", b["pack"])
        self.assertTrue(any("migration" in r["text"] for r in b["rules"]) or
                        any("persistent" in i["text"] for i in b["invariants"]), b["invariants"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
