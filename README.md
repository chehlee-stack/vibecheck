# VibeCheck

An auditor for codebases built with AI agents. Point it at a repository and it answers one
question: **what happens when the next agent edits this?**

```bash
python3 -m vibecheck scan .                          # audit + interactive HTML report
python3 -m vibecheck blast . "replace the inventory system"   # blast radius + agent brief
python3 -m vibecheck check . --fail-on high          # CI gate
```

No dependencies. Python 3.9 and `git`, nothing else. Nothing leaves the machine, and no model
is called unless you ask for one.

## What it does that a linter does not

A linter reads a file. VibeCheck reads the *seams between* files, because that is where agents
break things:

- **String contracts.** Screen names passed to a router, event names looked up in a handler
  table, the keys a handler reads out of a payload, save-file keys, signals, meta keys, groups,
  input actions. It follows each key from the site that sends it to the table that routes it —
  across files, through forwarding functions — and reports the ones that do not meet.
- **Documented boundaries, checked transitively.** If `AGENTS.md` says a model is free of `UI`
  and `Game`, VibeCheck searches the dependency graph for a path anyway. An architecture test
  that greps each file's own text cannot see a leak that arrives through a helper.
- **Members that no longer exist.** `Game.seasonal_event` after the refactor that moved it —
  the trace a rename leaves when it misses a caller.
- **Build traps.** A compile-time `preload` of a file the export filter strips: fine on the
  desk, a blank screen on the device.
- **Prompt archaeology.** Stale names in the docs an agent loads first, prompts marked
  do-not-run still sitting where an agent will find them, elision markers and chat voice left
  in source.
- **What history knows.** Files that change together with no reference between them.

## Output

| Command | What you get |
|---|---|
| `scan` | `report.html` (self-contained), `vibecheck.json`, `review-prompt.md`, one handoff pack per `--ask` |
| `blast` | The brief, on stdout, ready to paste into Claude Code or Codex |
| `check` | Exit 1 on agent-risk findings at or above a severity, with `file:line` lines |
| `timeline` | The score at every commit |
| `backtest` | How well the blast radius predicted the repository's real commits |

The report holds the **Vibe Debt Score** (six categories, every threshold shown), the **Do Not
Let AI Touch This** ranking, the **Repo MRI** dependency graph, and a blast-radius box you can
type any change into — the same engine as the CLI, ported to the page and checked against it
byte for byte.

### The Agent Handoff Pack

The point of the blast radius is the brief it writes: where the change goes, what will
probably break and why, what to read first, the invariants to preserve (with the lines that
pin them), the rules the repository's own docs already state, the checks to run, and when to
stop:

```
- The change needs an edit outside these files, beyond updating a call site: …
- The diff grows past about 441 changed lines or 14 files.
- Saved data would change shape. Stop and ask; if approved, follow the project's migration rule.
- A string key, signal or payload field from section 4 is renamed in some of its sites but not all.
```

## Does it actually predict anything?

`vibecheck backtest` replays the repository's own history. Every commit is scanned **at its
parent**, with only older history, and the prediction is scored against the files that commit
really touched. On this project's first corpus (a 41-commit Godot game, 21 eligible commits):

| Given | Method | recall@5 | recall@10 |
|---|---|---|---|
| the file the commit changed most | **VibeCheck** | **0.48** | **0.65** |
| | static graph only | 0.41 | 0.62 |
| | co-change only | 0.45 | 0.62 |
| | same directory | 0.31 | 0.48 |
| | random | 0.16 | 0.40 |
| only the commit's subject line | **VibeCheck** | **0.47** | **0.73** |

The graph's weights were set before the backtest ran and were not tuned on it. With one
repository the gap to the single-signal baselines is within noise; the gap to random and to
same-directory guessing is not.

## Scoring

Six categories — coupling, duplication, god objects, agent-edit risk, test protection, prompt
archaeology — each the mean of named components, each component a measured value mapped onto
0–100 between two thresholds the report prints next to it. Agent instructions and an
architecture test each earn a credit. **Scoring is v0: the thresholds are judgment, not a fit
to a corpus**, and the report says so wherever the number appears.

## Language support

| Language | How |
|---|---|
| GDScript, `.tscn`/`.tres`, `project.godot`, export presets | Full: tokenizer, symbols, contracts, scene connections, export rules |
| Python | Full, through the standard library's parser |
| JS/TS, C#, shaders, Go, Rust, Swift… | Tokenizer: size, functions, imports, duplication, smells |

The Godot support is deepest because the first corpus was a Godot game. The architecture is a
per-language adapter producing one `FileFacts` shape; everything downstream is language-neutral.

## Optional: a review by Claude

```bash
pip install anthropic          # the only dependency, and only for this
python3 -m vibecheck scan . --review
```

The deterministic scan writes `review-prompt.md` — the measured facts with their file and line
— whether or not a model is called. `--review` sends that pack to Claude (`claude-opus-5`) and
folds the reply into the report. Everything else runs offline.

## Layout

```
vibecheck/
  repo.py        read any commit without checking it out
  lang/          gdscript · godot_res · python_lang · generic
  project.py     parse, resolve references, assign roles, content-addressed cache
  contracts.py   string contracts: routing tables, payload shapes, signals, keys
  boundaries.py  rules written in agent docs, checked against the graph
  history.py     churn and co-change from git log
  graph.py       one weighted graph from four kinds of evidence
  clones.py      duplication (winnowing over normalized tokens)
  smells.py      AI leftovers, doc drift, dead prompts
  risk.py        per-file metrics and the Do-Not-Touch ranking
  score.py       the Vibe Debt Score
  blast.py       blast radius and the handoff pack
  backtest.py    predictions scored against real commits
  report.py      renders template.html with the scan embedded in it
tests/           python3 -m unittest discover tests
```
