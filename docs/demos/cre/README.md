# Build Mill demos for commercial real estate — runbook and index

> Idea documents only. Nothing here changes the app, a table, or a behavior, so no story governs it. Each `0N-*.md` is written to be lifted into a Build Mill **project template** later (Settings → Project templates → copy the default → paste that file's *Agent Instructions* block as the document; apply its *Project settings* to the project after creation).

## The thesis

Almost everything in a CRE firm's modernization program is already **text in a repository**: dbt models, SQL, Python ingestion, Power BI semantic models (PBIP/TMDL are plain text and diff cleanly), Fabric notebooks, Terraform. That is exactly the shape Build Mill's gates were built for. The thing a CRE firm actually worries about is not "can AI write code" — it is *a number in an investor report changed and nobody can say who approved it*. Build Mill's answer is structural: every change is a work item, every plan and PR passes a human gate, and every decision is in the approvals audit with an actor and a timestamp.

Lead with that. Then show projects in three tiers.

## The five demos

| # | Demo | Tier | What it sells | File |
|---|------|------|---------------|------|
| 1 | Legacy report conversion at volume | 1 · Data platform | The tedious part of every warehouse migration, shaped as a queue; cost per report | [01-legacy-report-conversion.md](01-legacy-report-conversion.md) |
| 2 | Power BI as code | 1 · Data platform | A DAX diff reviewed like code; a bulk rename no human wants | [02-power-bi-as-code.md](02-power-bi-as-code.md) |
| 3 | Broker rent roll & T-12 normalizer | 1 · Data platform | **The live demo** — bring one ugly rent roll, write the story in the room | [03-rent-roll-normalizer.md](03-rent-roll-normalizer.md) |
| 4 | Waterfall & promote calculator | 2 · CRE applications | Tests read the firm's own Excel model; the full release path to production | [04-waterfall-calculator.md](04-waterfall-calculator.md) |
| 5 | Text-to-SQL over the warehouse, with evals | 3 · AI systems | "How do we know the chatbot isn't lying" — every CFO question is a test case | [05-text-to-sql-evals.md](05-text-to-sql-evals.md) |

Demos 1, 2 and 5 share one synthetic portfolio dataset: [00-shared-cre-dataset.md](00-shared-cre-dataset.md). Build that first.

Dropped from the analysis, and why: source-system connectors (Yardi/MRI/Argus need real endpoints or elaborate fakes — the rent roll normalizer covers the "one story per source" shape without them); data-quality suites (folded into #1's reconciliation tests and the planted anomalies in the dataset); debt & covenant monitor and lease abstraction UI (good products, weaker *live* demos than #4); document intelligence (needs PDFs, OCR, and a review queue — too heavy for a first meeting). All of them are natural *second-meeting* stories on the same projects.

## Which factory surface each demo shows

| Surface | 1 Reports | 2 Power BI | 3 Rent roll | 4 Waterfall | 5 Text-to-SQL |
|---|---|---|---|---|---|
| Story → plan gate → code gate → PR | ● | ● | ● | ● | ● |
| Feature with PRD gate → breakdown into stories | | | | ● | |
| Chore (one-shot, no plan) | ● | ● | | | |
| Bug (RCA → fix) | ● | ● | | | |
| Pre-submit test evidence in review | ● | ● | ● | ● | ● |
| Factory-run automated suite (JUnit) | | | | | ● |
| Deployment → release cut → UAT → sign-off → promote | | | | ● | |
| Per-run cost, cost per unit of work (Costs room) | ● | | ● | | ● |
| Approvals audit ("who approved what, when") | ● | ● | ● | ● | ● |
| Attached document on a work item | | | ● | | |
| Project-env secret never in the workspace | | | | | ● |

## How to run the demo

Don't present a menu. Ask them for **one artifact** in the meeting: a broken SSRS report, an ugly rent roll, one sheet from a legacy Excel model. Attach it to a work item, write the story live, dispatch the plan run, and while it runs walk the room through a project that already has a backlog merged (#1 or #2). When the plan comes back, **reject it once** with a specific comment — the wow is not that AI wrote code, everyone has seen that. The wow is the plan you rejected, the retry that carried your comment verbatim, the PR with the tests, and the audit trail of who approved what. These firms live under fund audits, LP due-diligence questionnaires and SOC-style controls; the audit tab is the product.

The reference script is in [03-rent-roll-normalizer.md](03-rent-roll-normalizer.md) § *Live demo script*. Every other file has its own version.

Order of a 45-minute meeting:

1. Thesis (3 min) — their program is text in a repo; the question is who approved the number.
2. Ask for the artifact (2 min) — attach it, write the story, dispatch. Clock starts.
3. Show #1 or #2 with a merged backlog (10 min) — the Costs room with cost per report; the audit tab; a merged PR with its test evidence.
4. Plan is back (5 min) — read it aloud, reject once with a specific comment.
5. Show #4's release page (5 min) — cut → UAT → test cases → sign-off → promote; immutability; the same commit all the way through.
6. Re-plan is back (5 min) — approve; dispatch code.
7. Show #5's eval suite (5 min) — "how do we know it isn't lying"; adding a case is a story.
8. PR is up (5 min) — the diff, the tests, the pre-submit evidence, the cost of this one run.
9. The two arguments and the close (5 min).

## The two arguments to have ready

**Cost per unit of work.** Their alternative is a systems integrator billing $180 an hour to convert 200 reports. Every run in Build Mill records tokens in/out and `cost_usd` per model call; the Costs room groups by project, agent, and work-item type. That gives them a *number per report*. Put the number next to the SI's hourly rate — it's a CFO conversation, not an IT conversation.

**Capacity, not project.** Sell it as a standing intake for the 300-item backlog nobody staffs: the small integrations, the one-off analyst tools, the data-quality rules that keep getting deferred, the new broker rent-roll format that arrives with every deal. That is a different budget line from the warehouse program, and it is usually easier to get.

## The caution to name before they name it

Their IT security person will ask, in the first ten minutes, how a run tests a dbt model or a Power BI change **without touching production data**. The answer, and it holds for all five demos:

- Every demo repo carries its own data — a synthetic portfolio in DuckDB and CSV — so a run needs no warehouse credential at all to build and test.
- Where a real dev/UAT database is wanted, its connection string is a **project-environment secret**: the value lives in the factory's private storage bucket, never in Postgres, never in the workspace as a file, injected into the agent process environment only for the claimed run and redacted from logs. The manager sets it once; the agent never sees a production credential because none is configured.
- Pre-submit test evidence is *worker-reported* and the review page says so — it is a signal, not proof. Suites that gate a release are **factory-executed** on the UAT deployment, and a human still signs off.
- The runner is on the operator's own machine, inside their perimeter, and can be fenced with the runner command policy (allow / require-approval / deny by pattern) if they want commands like `dbt run --target prod` to be impossible.

Say those four things unprompted and the security conversation is over.

## Preparing the meeting

- Build the shared dataset and demos 1, 2 and 4 to a state where each has 3–6 *merged* stories with real runs behind them, so the Costs room and the audit tab have history to show.
- Keep #3 with its three pre-built formats merged and the backlog otherwise empty — that is the project the visitor's artifact lands in.
- Register #4's UAT and Production deployment targets and cut one release end-to-end the day before, so the release page has a real promoted build.
- Have the runner box ready: Python 3.12, `dbt-duckdb`, `pytest`, `openpyxl`, `duckdb`, `uvicorn`, and Power BI Desktop for #2's validation. The runner runs commands directly on that machine — if a tool is missing, the run fails at the pre-submit gate, visibly.
- Set the project's `auto_approve_*` flags **off** on every demo project. Auto-approve removes exactly the gate that is the headline.
- Confirm the four agent CLIs configured for the runner (Claude Code is the one to demo with) and that the org's LLM provider key is set — otherwise the first live run stalls in the pool.
