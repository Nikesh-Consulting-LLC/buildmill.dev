# 05 · Text-to-SQL over the warehouse, with an eval harness

## 1. Pitch

**Tier 3 — build the AI, don't just talk about it.** They said "agents." The way to show you take that seriously is to treat the agent as a software project with a test suite. A text-to-SQL assistant over the portfolio warehouse is the obvious first agent — the CFO wants to ask "what was NOI last quarter versus budget" without opening Power BI — and the obvious way it dies is the question that kills most internal AI pilots at investment firms: *how do we know the chatbot isn't lying to us?* The answer here is structural: **every question the CFO has ever asked is an eval case with a known-correct answer; every new question is a story; the suite runs in every release**, and a release can't be signed off while a case fails.

**Demo moment:** the eval report — 24 questions, 24 correct, cost per answer in cents — as a factory-run test suite on the release page; then a story that adds a 25th question, whose plan says how the agent's schema notes must change for it to pass, whose PR adds the case, and whose test evidence shows all 25 green.

## 2. Template card

- **name:** `CRE · Text-to-SQL agent with eval harness (DuckDB warehouse)`
- **description:** A schema-aware text-to-SQL agent over a CRE portfolio warehouse (DuckDB in-repo), read-only, with an eval harness where each business question is a case with a known answer; the harness emits JUnit and runs as a factory automated suite. Every new question is a story; the LLM key is a project-environment secret.
- **category:** `AI systems`

## 3. Repo scaffold

```
demo-cre-warehouse-qa/
├── AGENTS.md
├── README.md                             # what it does, how evals work, how to add a question
├── pyproject.toml                        # package `warehouse_qa`; deps: anthropic, duckdb, pydantic, pyyaml, typer, junit-xml (or a tiny writer)
├── data/                                 # vendored from 00-shared-cre-dataset (≤ 5 MB)
│   ├── portfolio.duckdb
│   └── README.md
├── warehouse_qa/
│   ├── schema_notes.md                   # PRE-BUILT: the agent's grounding — tables, grain, joins, business definitions (NOI, occupancy, DSCR…), gotchas from data/README.md
│   ├── schema.py                         # PRE-BUILT: introspects DuckDB (tables, columns, types, FKs) → compact DDL text for the prompt
│   ├── agent.py                          # PRE-BUILT: question → {sql, explanation, tables_used} via the Anthropic SDK; structured output; refuses non-SELECT
│   ├── guard.py                          # PRE-BUILT: SQL allow-list — single SELECT, no DDL/DML/ATTACH/COPY, LIMIT cap, referenced tables ⊆ allow-list
│   ├── run_sql.py                        # PRE-BUILT: read-only DuckDB connection, timeout, row cap, returns rows + column names
│   ├── answer.py                         # PRE-BUILT: question → sql → rows → short natural-language answer that quotes the numbers
│   └── cli.py                            # `wqa ask "..."`, `wqa eval`, `wqa eval --case <id>`
├── evals/
│   ├── cases/
│   │   ├── 001_portfolio_noi_last_quarter.yaml
│   │   ├── 002_occupancy_by_property_latest.yaml
│   │   ├── …                             # 20 PRE-BUILT cases; the demo adds more
│   │   └── 020_rent_cap_expiring_12mo.yaml
│   ├── run.py                            # PRE-BUILT: runs every case N times, compares, writes reports/junit.xml + reports/summary.md (incl. tokens & $ per case)
│   ├── compare.py                        # PRE-BUILT: value tolerance, set/row-hash comparison, column-order-insensitive
│   └── golden.py                         # PRE-BUILT: helper that computes the expected answer from a hand-written SQL in the case (so cases stay honest)
├── reports/                              # gitignored except a committed baseline summary for the README
├── tests/
│   ├── test_guard.py                     # PRE-BUILT: DML/DDL/multi-statement/`ATTACH` rejected; LIMIT injected
│   ├── test_compare.py
│   └── test_agent_offline.py             # PRE-BUILT: prompt assembly + parsing with a recorded response; no network
└── docs/decisions/                       # one page per non-obvious rule (e.g. "occupancy is physical, by units, not sf")
```

Case file shape (`evals/cases/*.yaml`):

```yaml
id: 001_portfolio_noi_last_quarter
question: "What was portfolio NOI last quarter, and how did it compare to budget?"
expected:
  kind: value                # value | rows | scalar_set
  sql: |                     # the human-written oracle; golden.py runs it against data/portfolio.duckdb
    select sum(actual) as noi_actual, sum(budget) as noi_budget ...
  tolerance: 0.005
must_use_tables: [gl_entries, budgets]
must_not_touch: [loans]
notes: "'Last quarter' is relative to max(period) in the data, not today's date."
```

The **agent** (`agent.py`): official `anthropic` Python SDK, model `claude-opus-5` (thinking on by default — no `thinking` parameter), `output_config.effort` set to `medium` for cost with `high` on failure retry, **structured output** (`output_config.format` JSON schema: `sql`, `explanation`, `tables_used`) so no regex parsing, `stop_reason` checked before reading content, `usage` captured per call for the cost column. Prompt = system (role + `schema_notes.md` + compact DDL, all stable → prompt-cached) + the question. Nothing else: no tools, no loop, no memory. That's deliberate — a text-to-SQL agent that's *one call and a guard* is one that can be evaluated.

Pre-built before the meeting: everything above with **20 cases green** and a committed baseline `reports/summary.md`; the suite registered in the factory and run at least once on a release so the release page has a suite result. Size ≈ 6 MB.

Each **story** adds: one case file (question + oracle SQL + tolerance), whatever `schema_notes.md` change makes the agent get it right, and a `docs/decisions/` page if a business definition had to be pinned.

## 4. Agent Instructions (paste-ready)

```markdown
## Project overview
A text-to-SQL assistant over a CRE portfolio warehouse (DuckDB in-repo, `data/portfolio.duckdb`) with an eval harness. Every business question the assistant must answer is a case under `evals/cases/` with a human-written oracle SQL; the harness runs the assistant, compares, and emits JUnit. Work items are almost always "add this question as a case and make it pass" — which usually means improving `warehouse_qa/schema_notes.md` (business definitions, joins, gotchas), not the code. The assistant is one model call plus a SQL guard; keep it that way.

## Tech stack
Python 3.12; `anthropic` (official SDK; model `claude-opus-5`, structured output via `output_config.format`, no `thinking` parameter — thinking is on by default); `duckdb` (read-only connection); `pydantic` v2; `typer` CLI; `pytest` for unit tests; the eval harness is its own runner (`python -m evals.run`).

## Commands
- `pip install -e .[dev]`
- `python -m pytest -q` — unit tests, no network (< 10 s)
- `python -m evals.run` — full eval, needs `ANTHROPIC_API_KEY`; writes `reports/junit.xml` and `reports/summary.md`
- `python -m evals.run --case 021_...` — one case, 3 repetitions
- `wqa ask "What was NOI last quarter?"` — try a question by hand

## Run commands
Before you submit: `python -m pytest -q` green, then `python -m evals.run` green for **all** cases including yours (each case runs 3 times; all 3 must pass — a flaky pass is a fail). Paste the summary table for your case (pass count, tokens, cost) into the PR description. If `ANTHROPIC_API_KEY` is not present in your environment, stop and say so via clarification instead of skipping the eval. Report the pre-submit command, exit code and counts through the gate.

## Testing expectations
A case is done when its oracle SQL runs and returns a sensible answer on `data/portfolio.duckdb`, the assistant's answer matches within tolerance on 3/3 runs, `must_use_tables` ⊆ tables actually referenced, and nothing in `must_not_touch` is referenced. Fix failures by improving `schema_notes.md` (definitions, join paths, gotchas from `data/README.md`) or `schema.py`'s DDL rendering — not by special-casing the question in code and never by editing the oracle to match the assistant. Guard tests must keep passing: single `SELECT` only, no DDL/DML/`ATTACH`/`COPY`, LIMIT enforced.

## Environment setup
`ANTHROPIC_API_KEY` is a project-environment secret provided to the run process; never write it to a file, never print it, never commit a `.env`. `WQA_MODEL` (plain, default `claude-opus-5`) and `WQA_EFFORT` (plain, default `medium`) may be set. The warehouse is the vendored DuckDB file — there is no external database.

## Things to avoid
- Don't add tools, retries-until-it-passes, or agent loops to make a case pass; if the model can't get it from the schema notes, the notes are wrong.
- Don't weaken `guard.py` to let a query through.
- Don't change `compare.py` tolerances per case; change the case's own `tolerance` and justify it in `notes`.
- Don't hardcode dates; "last quarter" is relative to `max(period)`.
- Don't put more than the current schema notes and DDL in the system prompt — it must stay cacheable and small.
- Don't edit `data/`.

## Permissions or boundaries
Add cases under `evals/cases/`, edit `warehouse_qa/schema_notes.md` and `docs/decisions/`; ask via clarification before changing `agent.py`, `guard.py`, `compare.py`, or `evals/run.py`. Never touch `data/` or the committed baseline summary.
```

## 5. Project settings

- **Pre-submit gate command:** `python -m pytest -q && python -m evals.run` (evidence: unit counts plus one JUnit case per eval question — the review page shows N passed / 0 failed for the whole eval).
- **Project environment:**
  - `ANTHROPIC_API_KEY` — `secret` (the only secret in all five demos; say so out loud — it's the beat where you explain the private bucket, process-env injection, and log redaction).
  - `WQA_MODEL` — `plain`, `claude-opus-5`.
  - `WQA_EFFORT` — `plain`, `medium`.
- **Automated suite (the point of this demo):** `layer: api`, run command `python -m evals.run --junit reports/junit.xml`, `results_path: reports/junit.xml`, `run_on_uat: true`, `run_on_prod: false`, `timeout_minutes: 15`, `blocks_signoff: false` initially — **flip it to true on stage** and say "now a release cannot be signed off while any question is answered wrong." The suite runs on the release's pinned commit; results show on the release page.
- **Deployment:** none required (the suite runs from the factory's suite runner). Optional second-meeting story: a Streamlit or FastAPI `/ask` deployed to UAT with the release path.
- **Gates:** `auto_approve_*` **off**.
- **Learnings to pre-seed:** "Occupancy is physical (units occupied / units total) unless the question says 'economic' or 'by square feet'." and "Revenue for NOI comes from `gl_entries` 4xxx accounts, not from `charges`; they differ by design at Northgate month 22."

## 6. Story backlog

Twenty cases pre-built and green; the backlog below is what the room sees. Merge 1–2 before the meeting so there's a "question added by a story" in history; keep 3–6 in `ready`.

| # | Type | Title | Acceptance criterion |
|---|---|---|---|
| 1 | story *(merged)* | Case: **portfolio NOI last quarter vs budget** | Case `021` added with oracle SQL; passes 3/3; `must_use_tables: [gl_entries, budgets]`; schema note added defining NOI accounts. |
| 2 | story *(merged)* | Case: **leases expiring in the next 18 months over 10,000 sf** | Case `022`; the month-to-month lease with null `end_date` is excluded and the rule is written in `docs/decisions/expirations.md`; passes 3/3. |
| 3 | story | Case: **DSCR by loan, trailing 12, next to covenant, flag breaches** | Case `023`; oracle uses `v_dscr_by_loan`; assistant must return one row per loan with `dscr`, `covenant`, `breach` boolean; passes 3/3. |
| 4 | story | Case: **rate caps expiring within 12 months and the exposure** | Case `024`; `must_not_touch: [tenants, leases]`; passes 3/3. |
| 5 | story | Case: **top 10 tenants by annualized rent, rolled up to parent company** | Case `025`; schema note explains `parent_company`; the assistant answers by parent, not subsidiary; passes 3/3. |
| 6 | story | Case: **delinquency over 60 days by property, with tenant count** | Case `026`; buckets relative to `max(period)`; passes 3/3. |
| 7 | story | **Guardrail: refuse and explain non-SELECT / cross-table intents** | New negative cases (`neg_001_delete`, `neg_002_update_rent`) assert the assistant returns a refusal object, not SQL; `guard.py` unchanged; unit tests extended. |
| 8 | story | **Cost & latency column in the eval report** | `reports/summary.md` shows tokens in/out, cache-read tokens, `$` per case (from `usage` and a rate table), p50 latency; committed baseline updated. |
| 9 | story *(stretch)* | **MCP server `query_warehouse` for analysts' Claude / Copilot** | A stdio MCP server exposing `ask(question)` and `run_sql(select)` behind the same guard; README shows connecting Claude Desktop; the eval harness gains a case that goes through the MCP path. |
| 10 | **bug** | "Occupancy %" answers differ between "by property" and "portfolio" (weighted vs unweighted) | RCA finds the schema note is ambiguous; fix pins portfolio occupancy as unit-weighted in `schema_notes.md` and `docs/decisions/occupancy.md`; both cases pass 3/3. |

Not the live demo (that's #3); shown for the eval suite and the release gate.

## 7. Live demo script

1. Open the release page for #4's promoted release (or this project's own last release): the suite result — 22 cases, 22 passed, factory-run on the pinned commit — next to the manual test cases and the sign-off. "This is how you know the chatbot isn't lying: every question anyone has cared about is a test that runs before a release."
2. Open the review page of merged story 2: the pre-submit strip shows the eval count; open the PR — a YAML case, an oracle SQL, and a two-line change to `schema_notes.md`. "The fix for a wrong answer is usually a better definition, and the definition is under review."
3. Ask the CFO in the room for a question. Write story 3 (or their question) live: title, AC pasted from row 3. Dispatch the plan.
4. While it plans, run `wqa ask` on their question in a terminal (or the CLI page) so they see the SQL and the answer *before* it's a case, and open `evals/reports/summary.md` to show cost per question in cents.
5. Plan comes back. **Reject once**: "Don't add a special case in `agent.py`; add the DSCR definition to `schema_notes.md` and let the model derive the SQL — the whole point is that definitions are reviewed, not code paths." Retry carries it.
6. Approve; dispatch code. Open the project settings → Environment: `ANTHROPIC_API_KEY · Set · <fingerprint>` — "the key is in the factory's private bucket, injected only into the run process, never in the repo, redacted from logs."
7. PR lands: the case file, the note change, evidence N+1 passed. Flip the suite's `blocks_signoff` to true and cut a release: "from now on a wrong answer blocks production."

## 8. Security / credentials answer

The warehouse is a DuckDB file in the repo, opened read-only, behind a SQL guard that permits a single `SELECT` with a row cap and refuses DDL, DML, `ATTACH` and `COPY` — the assistant cannot modify or exfiltrate anything, and there is no production database anywhere in the picture. The one credential, the LLM API key, is a project-environment secret: stored in the factory's private storage bucket, injected into the run's process environment for the duration of a claimed run only, never written to the workspace, redacted from logs; a real deployment would put the same assistant in front of a **read-only replica or a dev warehouse** through the same mechanism.

## 9. Prep & risks

- Runner box: Python 3.12, `pip install -e .[dev]`; the eval needs network to the Anthropic API — Build Mill's runner does not block egress, so this works, but confirm the org's key has headroom for ~25 cases × 3 runs × a few PRs.
- Keep the system prompt stable and cacheable (schema notes + DDL first, question last) — cost per question is a slide; make it small.
- Write oracle SQL by hand and *check it* against the planted anomalies in `data/README.md` — an eval with a wrong oracle is the one embarrassing failure here.
- Rehearse the CFO-question moment with five plausible questions; some will need a schema note first — that's a fine live story, but know which.
- If the API is slow or down in the meeting: the merged history and the release page carry the demo; don't run the live eval.
- Repetition count 3 keeps the demo honest and the cost small; don't raise it for the meeting.
- The MCP stretch story is a crowd-pleaser for "their analysts' Claude" but adds a second surface — keep it a story, not a pre-built.
