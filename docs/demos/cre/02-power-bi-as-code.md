# 02 · Power BI as code

## 1. Pitch

**Tier 1 — the data platform work they're already paying for.** Power BI Project files (PBIP) put the semantic model on disk as TMDL — plain text: tables, columns, relationships, and every DAX measure in a file that diffs cleanly in git. Most BI teams have never seen a Power BI change reviewed like code; the measure just changes and the report is different on Monday. Here, "add a measure", "fix the broken relationship", "standardize formatting across the model" are stories, and each lands as a PR where **the DAX diff is the review**. The other half of the pitch is the chore: bulk-renaming forty measures to a naming convention across the model, a task no human wants to do by hand and no one wants to do without a diff.

**Demo moment:** a PR whose diff is a DAX measure, sitting behind an approve button, with a lint gate that says every measure has a description and a format string — and a merged chore whose diff touched 40 measures in one review.

## 2. Template card

- **name:** `CRE · Power BI semantic model as code (PBIP/TMDL)`
- **description:** A Power BI Project (PBIP) whose semantic model is TMDL in git; stories add measures, fix relationships and standardize the model, each reviewed as a DAX diff with a lint gate. Data is a vendored synthetic CRE portfolio, so no gateway or workspace credential is needed.
- **category:** `Data platform`

## 3. Repo scaffold

```
demo-cre-powerbi-model/
├── AGENTS.md
├── README.md                                   # how to open in Desktop, how the lint works, naming convention
├── data/csv/*.csv                              # vendored from 00-shared-cre-dataset (import source)
├── CRE Portfolio.pbip                          # the project file Desktop opens
├── CRE Portfolio.SemanticModel/
│   ├── definition.pbism
│   ├── .platform
│   ├── diagramLayout.json
│   └── definition/
│       ├── database.tmdl
│       ├── model.tmdl                          # culture, defaults, annotations
│       ├── expressions.tmdl                    # DataFolder parameter → data/csv (relative; no absolute paths)
│       ├── relationships.tmdl                  # every relationship — where the "broken cardinality" story lives
│       ├── cultures/en-US.tmdl
│       └── tables/
│           ├── Date.tmdl                       # calculated date table, marked as date table
│           ├── Properties.tmdl
│           ├── Units.tmdl
│           ├── Tenants.tmdl
│           ├── Leases.tmdl
│           ├── Charges.tmdl                    # fact — rent roll billing
│           ├── GL Entries.tmdl                 # fact — NOI
│           ├── Budgets.tmdl
│           ├── Loans.tmdl
│           ├── Occupancy Snapshots.tmdl
│           └── _Measures.tmdl                  # measures table — ~40 measures pre-built, half named badly on purpose
├── CRE Portfolio.Report/                       # one small report page so Desktop opens something (optional)
│   ├── definition.pbir
│   └── report.json
├── tools/
│   ├── tmdl_check.py                           # PRE-BUILT: parses TMDL, enforces the rules below
│   ├── tmdl_parse.py                           # tiny line-based TMDL reader (indent + `key: value` + measure blocks)
│   └── conventions.md                          # the naming convention the lint enforces
├── tests/
│   ├── test_tmdl_check.py                      # pytest: rules pass on the model; unit tests for the parser
│   └── test_model_shape.py                     # every table has a partition; every relationship resolves; date table marked
└── requirements.txt                            # pytest, pyyaml
```

Pre-built before the meeting: the model with ~10 tables and ~40 measures where about 20 measures deliberately violate the convention (`Sum of amount_billed`, `Measure 3`, `NOI_ttm`, no descriptions, mixed format strings) so the chore has something to do; `relationships.tmdl` with one relationship set to the wrong cardinality on purpose (`Properties` ↔ `Charges` many-to-many through the wrong column); a lint that **passes** on the pre-built model because the violating measures are listed in `tools/lint_allowlist.yaml` — the chore's acceptance criterion is "delete the allowlist". Size ≈ 6 MB.

Lint rules (`tools/tmdl_check.py`, run by pytest):

1. Every measure has a `description` and a `formatString`.
2. Measure names match the convention: `Title Case Words`, no underscores, no `Sum of`, `Count of`, `Measure N`; percentages end in `%`; currency measures use the shared currency format string; trailing-twelve measures end in `TTM`.
3. Every relationship's `fromColumn`/`toColumn` names an existing table and column.
4. Every table has exactly one partition; the `Date` table carries `dataCategory: Time` and `isDateTableMarked`.
5. No measure references a column that does not exist (regex over `'Table'[Column]` tokens against the parsed tables — a cheap dangling-reference catch).
6. Measures live in `_Measures` only (no measures inside fact tables) — the "standardize" story.

Each **story** edits one or two `.tmdl` files and, when adding a measure, adds a row to `tests/measures_expected.yaml` (name, format string, description non-empty) so the lint has an oracle for what should exist.

## 4. Agent Instructions (paste-ready)

```markdown
## Project overview
A Power BI Project (PBIP) for a CRE portfolio, with the semantic model held as TMDL text under `CRE Portfolio.SemanticModel/definition/`. Work items change the model — add or fix DAX measures, correct relationships, standardize naming and formatting — and are reviewed as diffs. Data is the vendored CSVs in `data/csv/`; the model imports them through the `DataFolder` parameter. Nothing connects to a gateway, a workspace, or a warehouse.

## Tech stack
Power BI Desktop (PBIP + TMDL, the enhanced report/model format); DAX; Power Query M for the import partitions; Python 3.12 + pytest for the model lint in `tools/`. Optional on Windows: Tabular Editor 2 CLI for Best Practice Analyzer.

## Commands
- `pip install -r requirements.txt`
- `python -m pytest -q` — runs the TMDL lint and shape tests (< 5 s)
- `python tools/tmdl_check.py --explain` — prints every rule and every current violation
- Open `CRE Portfolio.pbip` in Power BI Desktop to see the model (manual; not something a run does)

## Run commands
Before you submit: `python -m pytest -q` must pass. If you added a measure, add its expected row to `tests/measures_expected.yaml`. If you changed a relationship, run `python tools/tmdl_check.py --explain` and paste the relationships section into the PR description. Report the command, exit code and counts through the pre-submit gate.

## Testing expectations
The lint is the gate: descriptions and format strings on every measure, the naming convention in `tools/conventions.md`, no dangling relationship or column reference, one partition per table, date table marked. Do not add a violating measure to `tools/lint_allowlist.yaml` to get green — the allowlist only shrinks. DAX correctness cannot be executed here, so write the DAX plainly, prefer `DIVIDE` over `/`, use `CALCULATE` with explicit filters, and explain the measure in its `description` in one sentence a finance user would accept.

## Environment setup
None. `expressions.tmdl` holds `DataFolder` as a relative path (`data/csv`) — never write an absolute path into any partition. No secrets exist in this project.

## Things to avoid
- Don't invent TMDL syntax. Every construct you need already appears in the repo (a measure with a multi-line expression, a relationship, a column, a partition) — copy the shape, including tab indentation. Desktop is strict about it.
- Don't touch `lineageTag` values on existing objects, and don't reuse one; new objects need a fresh GUID.
- Don't put measures in fact tables; they belong in `_Measures`.
- Don't rename a column (it breaks the report); rename measures only when the story says so.
- Don't edit `data/csv/`.

## Permissions or boundaries
Edit only under `CRE Portfolio.SemanticModel/definition/`, `tests/measures_expected.yaml`, and `tools/lint_allowlist.yaml` (removals only). Ask via clarification before changing `expressions.tmdl` or `model.tmdl`. Never modify the `.Report/` folder unless the story is about the report.
```

## 5. Project settings

- **Pre-submit gate command:** `python -m pytest -q`
- **Project environment:** none. (If the runner box has Tabular Editor 2: `TE2_PATH` as a `plain` entry so a stretch story can add a BPA step; not for the meeting.)
- **Automated suite:** none.
- **Deployment:** none. (A second-meeting story: publish via Fabric REST / `pbi-tools` with a service principal held as a project-env secret.)
- **Gates:** `auto_approve_*` **off**.

## 6. Story backlog

Merge 1, 2 and the chore (5) before the meeting so the DAX-diff PR and the 40-measure diff both exist to open. Keep 3, 4, 6 in `ready`.

| # | Type | Title | Acceptance criterion |
|---|---|---|---|
| 1 | story | Add **Physical Occupancy %** measure | `_Measures` gains `Physical Occupancy % = DIVIDE([Units Occupied], [Units Total])` with `formatString: 0.0%` and a one-sentence description; lint passes; expected row added. |
| 2 | story | Add **NOI TTM** and **NOI TTM YoY %** measures | Both measures use `DATESINPERIOD` over the marked `Date` table, are formatted (currency / `0.0%`), described, and the YoY measure returns blank (not error) where the prior period is empty; lint passes. |
| 3 | story | Fix cardinality on **Properties ↔ Charges** | `relationships.tmdl` relates `Charges` to `Properties` through `Units`→`Buildings`→`Properties` (or a `property_id` column added to the `Charges` partition via M), the direct many-to-many relationship is removed, and `test_model_shape.py` asserts no many-to-many relationships remain. |
| 4 | story | Add **Rent PSF** by asset class | `Rent PSF = DIVIDE([Annualized Base Rent], [Occupied SF])` with currency format; description states annualized basis; lint passes. *(Sets up bug 6.)* |
| 5 | **chore** | Bulk-rename all measures to the naming convention and delete the allowlist | Every measure in `_Measures.tmdl` matches `tools/conventions.md`; `tools/lint_allowlist.yaml` is empty; the PR body lists old → new names as a table; no DAX bodies changed except references to renamed measures. *(One-shot; the 40-line diff.)* |
| 6 | **bug** | **Rent PSF** returns blank for industrial properties | RCA finds industrial rents in `data/csv/leases.csv` are quoted annually (see `data/README.md`) so `[Annualized Base Rent]` multiplies by 12 twice and the `DIVIDE` denominator filter excludes them; fix normalizes in the `Leases` partition (M) or the measure, with a description update; lint passes and a new expected row pins the format. |
| 7 | story | Add descriptions to every measure that lacks one | Zero measures without `description`; each description is one sentence a finance user would accept; lint rule 1 has no allowlist entries left. |
| 8 | story | Standardize currency format strings across the model | Every currency measure uses the shared format string defined in `tools/conventions.md`; the PR body lists changed measures. |

Not the live demo; this project is shown with history on it (especially the chore's diff).

## 7. Live demo script

1. Open the merged PR for story 1. Show the diff: ~8 lines of TMDL, one DAX expression, one format string, one description. "This is a Power BI change under code review."
2. Open the chore's merged PR: 40 measures renamed in one reviewed diff, the old → new table in the PR body, no plan run — "a chore is one shot; the manager still approves the merge."
3. Open the review page for a merged story: the pre-submit strip (`pytest`, 0 failed) — the lint that says every measure has a description and format string.
4. Dispatch story 3 (the relationship fix). When the plan returns, **reject once**: "Don't add a many-to-many bridge — carry `property_id` into the `Charges` partition in M and relate one-to-many." Show the retry carrying the comment.
5. Approve the re-plan; dispatch code. Open the audit tab meanwhile.
6. When the PR lands, open the `relationships.tmdl` diff and the `--explain` relationships section pasted in the PR body.
7. If time: open the model in Power BI Desktop from the merged `main` to show it loads (pre-verified — see risks).

## 8. Security / credentials answer

The model imports vendored CSVs from the repo through a relative-path parameter; no gateway, workspace, service principal or warehouse credential exists in the project, so a run can neither read nor write anything outside its checkout. Publishing to a Fabric workspace would be a later story with a service principal held as a project-environment secret, injected only into that run.

## 9. Prep & risks

- **Desktop must open the PBIP.** Build the model in Power BI Desktop first, save as PBIP, and let *Desktop* emit the TMDL; then hand-plant the violations. Never hand-write TMDL from memory — the indentation and `lineageTag` rules are strict, and a model that won't open is the most embarrassing possible failure. Re-open the merged `main` in Desktop the morning of the meeting.
- The lint parser is deliberately small (indent-aware, `key: value`, `measure 'Name' = <expr>` blocks incl. triple-backtick multi-line DAX). Keep it that way; it only needs to be right for the shapes Desktop emits.
- Agent DAX cannot be executed on the runner box (no Analysis Services). Say so out loud: "the gate proves shape and convention; the human reviews the DAX; the number is verified when the release's UAT test cases run in Desktop." A stretch story can add Tabular Editor 2 BPA on the Windows runner.
- The runner box needs Python 3.12 + pytest only.
- Keep the report page tiny (or omit `.Report/`) so the repo stays under 10 MB.
