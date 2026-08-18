# 01 · Legacy report conversion at volume

## 1. Pitch

**Tier 1 — the data platform work they're already paying for.** Every CRE firm has a graveyard of SSRS reports, Excel workbooks and Access queries that the warehouse program must replace, and every one of them ends the same way: someone tedious rebuilds it in dbt and someone nervous checks that the new number matches the old one. That is a queue, and queues are what a factory is for. One story per report; each story produces the dbt model, its schema tests, and a **reconciliation test that proves the new number matches the legacy number to the penny** — or documents exactly why it shouldn't.

**Demo moment:** eight conversion stories queued, plans approved in a row, PRs landing with `dbt build` evidence, and then the Costs room grouped by work item showing a dollar figure *per report*. Put that next to a systems integrator at $180/hour.

## 2. Template card

- **name:** `CRE · Legacy report conversion (dbt on DuckDB)`
- **description:** Convert SSRS/Excel/Access reports into dbt models one story at a time, each with schema tests and a to-the-penny reconciliation against the legacy output. Ships with a synthetic CRE portfolio in DuckDB so no warehouse credential is needed to build or test.
- **category:** `Data platform`

## 3. Repo scaffold

```
demo-cre-report-conversion/
├── AGENTS.md                         # exported from Build Mill agent instructions (section 4)
├── README.md                         # what this is, how to run dbt, the reconciliation rule
├── dbt_project.yml                   # profile: cre_demo; seeds enabled
├── profiles.yml                      # dbt-duckdb, path: data/portfolio.duckdb   (checked in — no secrets)
├── packages.yml                      # dbt_utils, dbt_expectations
├── data/                             # vendored from 00-shared-cre-dataset (≤ 5 MB)
│   ├── portfolio.duckdb
│   ├── csv/*.csv
│   └── README.md
├── legacy/                           # the graveyard — the INPUT to every story
│   ├── ssrs/
│   │   ├── RentRollByProperty.rdl        # XML; embedded T-SQL in <CommandText>
│   │   ├── OccupancyTrend.rdl            # the one with UNION (no DISTINCT) — planted
│   │   ├── DelinquencyAging.rdl
│   │   └── LeaseExpirationSchedule.rdl
│   ├── excel/
│   │   ├── T12_by_Asset.xlsx             # SUMIFS over a pasted GL extract
│   │   ├── NOI_by_Property.xlsx
│   │   ├── Budget_vs_Actual.xlsx
│   │   └── Top_Tenants.xlsx              # ranks by tenant, not parent — planted
│   └── expected/                         # the OLD numbers, one CSV per report, as the legacy report emitted them
│       ├── rent_roll_by_property.csv
│       ├── occupancy_trend.csv           # contains the 12B double count
│       ├── t12_by_asset.csv
│       ├── delinquency_aging.csv
│       ├── lease_expiration_schedule.csv
│       ├── noi_by_property.csv           # contains the month-22 variance at Northgate
│       ├── budget_vs_actual.csv
│       └── top_tenants.csv
├── models/
│   ├── staging/                          # PRE-BUILT: stg_* over every raw table, one per table, tested
│   │   ├── stg_properties.sql … stg_gl_entries.sql
│   │   └── schema.yml
│   ├── intermediate/                     # PRE-BUILT: int_rent_roll_monthly, int_noi_monthly
│   └── marts/                            # EMPTY at start — every story adds one model + tests here
│       └── .gitkeep
├── seeds/legacy_expected/                # the legacy/expected CSVs registered as dbt seeds (so tests can join them)
├── tests/                                # singular tests; each story adds tests/reconcile_<report>.sql
│   └── generic/assert_equal_to_seed.sql  # PRE-BUILT macro-backed generic test: model == seed on keys, tolerance 0.005
├── macros/reconcile.sql                  # PRE-BUILT: reconcile(model, seed, keys, measures, tolerance)
└── docs/conversion-notes/                # each story writes <report>.md: source SQL, mapping, variances found
```

Pre-built before the meeting: the dataset, staging + intermediate models (all green), the `reconcile` macro and generic test, `legacy/` in full, an empty `marts/`. Size ≈ 6 MB.

Each **story** adds: `models/marts/<report>.sql`, its `schema.yml` entry (columns, `not_null`/`unique`/`accepted_values`, one `assert_equal_to_seed` against `legacy_expected.<report>`), `tests/reconcile_<report>.sql` for anything the generic test can't say, and `docs/conversion-notes/<report>.md`.

## 4. Agent Instructions (paste-ready)

```markdown
## Project overview
A dbt project that replaces a legacy reporting estate (SSRS `.rdl`, Excel workbooks) with tested dbt marts over a CRE portfolio warehouse. The warehouse is DuckDB in-repo (`data/portfolio.duckdb`) — synthetic, deterministic, and the only data you need. Every work item is "convert one legacy report": read the legacy source in `legacy/`, produce a mart that reproduces its numbers, prove it with a reconciliation test against `legacy/expected/<report>.csv`, and write down any variance you cannot and should not match.

## Tech stack
dbt-core 1.8+ with `dbt-duckdb`; DuckDB 1.x; packages `dbt_utils`, `dbt_expectations`. Python 3.12 only for the dataset generator in `tools/`. No warehouse other than the in-repo DuckDB file; the `profiles.yml` is checked in and contains no secrets.

## Commands
- `pip install -r requirements.txt` — dbt-duckdb and friends
- `dbt deps` — once per checkout
- `dbt seed` — loads `legacy/expected/*.csv` as `legacy_expected.*`
- `dbt build` — run + test everything (fast: < 60 s)
- `dbt build --select <model>+` — one mart and its tests
- `dbt docs generate` — refresh docs

## Run commands
Before you submit: `dbt deps && dbt seed && dbt build`. Everything must be green. If your mart's reconciliation test fails on a *documented* legacy defect (see `data/README.md` → "Planted anomalies"), do not weaken the test to match a wrong number: exclude the specific rows in the test with a comment naming the anomaly, and record the variance in `docs/conversion-notes/<report>.md`. Report the command, exit code and counts through the pre-submit gate.

## Testing expectations
Every mart has: `not_null` on its grain keys, `unique` on the composite key, and one `assert_equal_to_seed` (or a singular `tests/reconcile_<report>.sql`) comparing measures to `legacy_expected.<report>` on the report's grain with tolerance 0.005. A mart with no reconciliation test is not done. Staging models are pre-tested; don't loosen their tests.

## Environment setup
None required. `profiles.yml` points at `data/portfolio.duckdb`. If a project environment variable `WAREHOUSE_URL` is present, it names a *development* warehouse the manager has authorized for an optional second target (`--target dev`); never assume it exists and never point anything at production.

## Things to avoid
- Don't edit files under `legacy/` or `data/` — they are the inputs and the oracle.
- Don't rewrite staging or intermediate models to make a mart easier; add an intermediate model if you genuinely need one and test it.
- Don't "fix" a legacy number by matching it. Reproduce the report; surface defects.
- Don't hardcode dates — the reports are relative to `max(period)` in the data.
- Don't add Python models or external packages beyond `dbt_utils` / `dbt_expectations`.

## Permissions or boundaries
Work only in `models/marts/`, `tests/`, `docs/conversion-notes/`, and the mart's `schema.yml`. Ask (via clarification) before adding a new seed or macro. Never add credentials or a `.env` file to the repo.
```

## 5. Project settings

- **Pre-submit gate command:** `dbt deps && dbt seed && dbt build` (evidence appears on the review page as worker-reported pass/fail counts).
- **Project environment:** none required. Optional `WAREHOUSE_URL` (`secret`) if you want to show the "dev warehouse, never prod" answer with a real MotherDuck / Postgres / Snowflake dev target — leave unset for the meeting.
- **Automated suite:** none (the pre-submit gate is the whole test story here; the reconciliation is deterministic).
- **Deployment:** none.
- **Gates:** `auto_approve_prd/plan/code` **off**.
- **Learnings to pre-seed:** "Legacy `.rdl` files quote industrial rents annually; divide by 12 when the unit type is `industrial`." (Let the agent *submit* this learning from the first story and accept it live — it's a nice beat.)

## 6. Story backlog

Eight conversion stories, one bug, one chore. Merge stories 1–4 before the meeting; keep 5–8 in `ready` so there is a visible queue to dispatch in front of the room.

| # | Type | Title | Acceptance criterion |
|---|---|---|---|
| 1 | story | Convert **Rent Roll by Property** (SSRS) to `marts.rent_roll_by_property` | Mart matches `legacy_expected.rent_roll_by_property` on (property, unit) for sf, tenant, base_rent_monthly within 0.005; industrial rents converted from annual; conversion note written. |
| 2 | story | Convert **T-12 by Asset** (Excel) to `marts.t12_by_asset` | Revenue/opex/NOI per property match the seed within 0.005 for every property except Northgate month 22, which is excluded in the test with the variance (1,250.00) documented in the conversion note. |
| 3 | story | Convert **Delinquency Aging** (SSRS) to `marts.delinquency_aging` | Buckets 0–30/31–60/61–90/90+ per tenant match the seed; aging is relative to `max(period)`, not `current_date`. |
| 4 | story | Convert **Lease Expiration Schedule** (SSRS) to `marts.lease_expiration_schedule` | Expirations by quarter (count, sf, annual rent rolling off) match the seed; the month-to-month lease with a null end date is shown in a `mtm` bucket, not dropped, and the rule is documented. |
| 5 | story | Convert **NOI by Property** (Excel) to `marts.noi_by_property` | Monthly NOI per property matches the seed except the documented Northgate variance; reuses `int_noi_monthly` rather than recomputing from GL. |
| 6 | story | Convert **Budget vs Actual** (Excel) to `marts.budget_vs_actual` | Actual, budget, variance and variance % per property/account/period match the seed; variance % is null (not div-by-zero) where budget is 0. |
| 7 | story | Convert **Top Tenants** (Excel) to `marts.top_tenants` | Top-10 by annualized base rent matches the seed when ranked by *tenant*; a second column ranks by *parent company* and the conversion note explains why the legacy report understated Meridian Health. |
| 8 | story | Convert **Occupancy Trend** (SSRS) to `marts.occupancy_trend` | Units occupied per property/month equal `occupancy_snapshots`; the reconciliation against the seed **fails** on Riverside Commons from month 14 and the story documents the double count of unit 12B in the legacy `UNION`. |
| 9 | **bug** | Legacy occupancy trend double-counts unit 12B at Riverside Commons | RCA names the `UNION` without `DISTINCT` in `OccupancyTrend.rdl`; fix regenerates `legacy_expected.occupancy_trend` from the corrected query and story 8's reconciliation goes green with no test exclusions. *(Dispatch after 8 merges — it shows RCA → fix as a distinct shape.)* |
| 10 | **chore** | Add `dbt docs generate` and a `docs/` publish step to the repo's CI folder | `.github/workflows/dbt-docs.yml` exists and runs `dbt docs generate` on push to `main`; no plan run, one PR. |

Not the live demo (that's #3); this project is shown with history already on it.

## 7. Live demo script

1. Open the project's Work items filtered to `merged` — four converted reports, each with a PR and a green pre-submit strip. Open one PR: the mart, the `schema.yml`, the reconciliation test, the conversion note.
2. Open the Costs room, group by work item, filter to this project: **cost per report**. Say the number. Say "$180 an hour."
3. Dispatch stories 5, 6, 7 in a row. Show them enter the pool and get claimed.
4. Open the audit tab: every plan and code approval so far, with actor and timestamp. "This is what your fund auditor gets."
5. When story 5's plan returns, read it aloud. **Reject once**: "You are recomputing NOI from the GL — reuse `int_noi_monthly` so the T-12 and NOI marts can never disagree." Show the retry run carrying that comment verbatim.
6. Approve the re-plan; dispatch code. While it runs, open story 8's plan (pre-approved) — the one where the *legacy* report is wrong — and the bug that follows it. "The factory didn't hide the defect. It wrote it down."
7. When the PR lands: the diff, the test evidence strip, and the run's own cost line.

## 8. Security / credentials answer

The warehouse for this project is a DuckDB file committed to the repo — the agent builds and tests every model with no credential at all, on the operator's machine, with no network. If they later want a real development warehouse, its connection string is a project-environment secret injected into the run process only, never written to the workspace and never a production credential.

## 9. Prep & risks

- Runner box needs `dbt-core`, `dbt-duckdb`, `duckdb`; run `dbt build` once by hand from a fresh clone to prove `< 60 s`.
- Generate `legacy/expected/*.csv` **from the legacy queries as written** (including the defects) — that's the whole point; a script under `tools/` should produce them so you can regenerate.
- The `.rdl` files must be plausible: real `<DataSet>` / `<Query>` / `<CommandText>` structure with T-SQL that references table names an agent can map to the DuckDB schema. Write the T-SQL as a real SSRS author would (nested subqueries, `ISNULL`, `DATEDIFF`).
- The Excel workbooks must contain live formulas, not pasted values, so an agent has to *read* the logic.
- Don't pre-merge everything: an empty queue makes the "queue" argument invisible.
- If a plan comes back already correct, reject it anyway on a real preference (naming, reuse) — the rejection is the demo.
