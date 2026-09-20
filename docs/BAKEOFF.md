# GCAA build and UI bake-off

Baseline: `chehlee-stack/vibecheck`, commit `87d6a541127b5107e11622c9425d473e28592839`.
Candidate: `codex/gcaa-build-ui-bakeoff`. The baseline remains on `main`.

## Assessment

The useful product is the evidence-to-change-brief workflow. The current engine has
substantial Godot-specific reasoning, but the original delivery and coverage claims
were ahead of its verification. A polished risk score is only useful if the scan is
current, the graph includes the imports, and incomplete analysis is visible.

This candidate improves those boundaries and the report workflow. It does not
establish that GCAA can certify architecture quality or predict regressions across
arbitrary repositories, nor does it establish one model is universally better.

## Concrete findings and fixes

| Priority | Baseline problem | Candidate behavior |
|---|---|---|
| High | A reused URL clone fetched but left the scan pointed at local HEAD. The second scan could assess the first commit again. | URL scans resolve the fetched origin default branch (or explicit remote branch); refresh failures stop the scan. The checkout is not reset. |
| High | `from package import module` only linked the package. Tests importing modules that way did not protect the relevant module in the graph. | Imports retain member-module candidates, restoring dependency and test-reference edges. |
| High | The HTML title inserted the repository name without escaping. Embedded `<!--` was rewritten with an invalid JSON escape, which can blank the report. | Escaped titles and valid Unicode JSON escapes; hostile strings round-trip through the generated document. |
| Medium | The report called itself self-contained but requested Google Fonts and D3 from CDNs. | System typography and an inline SVG dependency map; zero external report assets. |
| Medium | Parser failures were recorded internally but omitted from findings and report coverage. | Incomplete analysis becomes a medium finding and appears in coverage metadata. |
| Medium | No installable package or CI workflow; browser/Python parity was asserted in prose without a committed parity test. | Installable wheel, `gcaa` and legacy entry points, package-data checks through installed scans, Python 3.9/3.12 CI, and six cross-engine scenarios. |
| Medium | Git batch input used filenames delimited by newlines. A valid tracked newline filename could desynchronize subsequent reads. | Batch reads use object IDs. A real temporary Git fixture verifies odd filenames and subsequent files. |
| Product | Long reading-first report, buried planner, severity-only filtering, game-specific examples on Python projects. | Overview, nearby change-planning action, sidebar/mobile navigation, text/category/severity filters, evidence-to-planner action, downloadable briefs, JSON export, and context-aware examples. |
| Credibility | The report inferred fixes from disappearing findings and declared significance against baselines without calculating it. | Distinguishes observed disappearance from a verified fix; reports backtest differences descriptively. |

## Controlled comparison

The UI comparison uses the **same original JSON** in both renderers: the content,
score and findings are unchanged. The third report reanalyzes the **same Git commit**
with the corrected import resolver, separating UI changes from analyzer changes.

| Measurement on the frozen commit | Original engine | Corrected engine |
|---|---:|---:|
| Architecture debt score | 35.5 | 30.0 |
| Test-protection debt | 95.8 | 61.8 |
| Resolved static edges | 86 | 101 |
| Findings | 4 | 4 |
| Test files identified | 1 | 1 |

Lower debt after this fix reflects better recognition of existing dependencies,
not newly written tests in the assessed commit. Test-reference reach is a heuristic,
not executed test coverage. Score weights and thresholds were not changed.

## Verification

- Baseline: 19 Python tests passed.
- Candidate: 27 Python tests passed, including refreshed-clone behavior, fetch
  failure, newline paths, parser failure visibility, package-member imports and
  safe/offline HTML rendering.
- Six requests compare Python and JavaScript entry points, impacted paths/kinds
  and byte-for-byte handoff text: save-system change, wardrobe rename, multiplayer,
  documented-rule conflict, empty request and unrelated terms.
- Node syntax checks pass.
- jsdom interaction checks pass for initialization, offline asset independence,
  severity/text filtering, clearing filters, finding-to-planner handoff, analysis,
  keyboard graph selection, graph search, zoom/reset and score expansion.
- Wheel built and installed in an isolated environment; installed command checked
  outside the repository (including report assets).
- Visual browser QA remains **unverified**: this environment's browser blocked
  local HTTP and file previews. DOM tests do not establish responsive layout,
  visual quality, native downloads or screen-reader behavior. Keep the PR a draft
  pending desktop/mobile browser review.

## Remaining work, in priority order

1. **Validate on a corpus.** Use multiple independent repositories and languages,
   held-out commits, explicit confidence intervals and negative controls before
   advertising predictive superiority or meaningful universal score thresholds.
2. **Deepen language adapters.** JS/TS and C# tokenization is not semantic resolution.
   Dynamic imports, runtime dispatch and framework conventions remain blind spots.
   Godot support remains substantially deeper than general-purpose support.
3. **Reduce dual-engine drift.** The extracted JavaScript engine now has parity
   checks, but two hand-maintained implementations remain. Expand fixtures when
   changing either engine; evaluate a shared intermediate representation later.
4. **Separate orchestration from analysis state.** `Scan` still aggregates many
   detector results, and `blast.py` / `contracts.py` remain large modules. Refactor
   behind scenario fixtures, rather than changing all interfaces in this UI pass.
5. **Large-repository performance and browser review.** The dependency map uses a
   stable directory layout and no force simulation, but SVG still renders all
   nodes/edges. Measure realistic large graphs and inspect narrow/mobile layouts.
6. **Current vs. committed state.** Local scans intentionally read committed HEAD.
   A distinct, labeled working-tree mode would help pre-commit use, but should not
   silently change reproducible snapshot semantics.

## Review commands

```bash
python3 -m pip install .
gcaa scan . --out gcaa-report
python3 -m unittest discover tests -v
npm ci --prefix tests --ignore-scripts
NODE_PATH=tests/node_modules node tests/ui-smoke.cjs gcaa-report/report.html
```

Open `gcaa-report/report.html` in a normal browser. No server or network is needed.
