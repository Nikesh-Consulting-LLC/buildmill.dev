# 04 · Waterfall & promote calculator — tests built from the Excel model, shipped through the release path

## 1. Pitch

**Tier 2 — CRE-specific software the firm wants but never prioritizes.** Every firm has *the* waterfall workbook — return of capital, preferred return, IRR hurdles, catch-up, promote splits — and everyone is a little afraid of it. The pitch is not "we'll rewrite your spreadsheet"; it's **"we'll build the engine and the tests will read expected values out of your spreadsheet"** — the Excel model is the oracle, and the PR proves the code matches it to the penny, on every scenario tab. This is also the demo that shows the *whole* release path: it's a **feature** (PRD gate → breakdown into stories), it deploys to a server, and a release goes cut → UAT → test cases → sign-off → promote, with the same pinned commit all the way and an audit entry at every step.

**Demo moment:** a test file that opens the firm's own workbook and asserts the engine's distributions equal cells on the *Waterfall* tab; a release page where UAT passed, a human signed off, and Production shows the same commit hash.

## 2. Template card

- **name:** `CRE · Waterfall & promote calculator (Excel-oracle tests, deployed API)`
- **description:** A Python distribution-waterfall engine with a small FastAPI service, whose tests read expected values from the firm's own Excel model. Deployed to UAT and Production through the factory's release path so a release shows the same pinned commit from cut to promote.
- **category:** `CRE applications`

## 3. Repo scaffold

```
demo-cre-waterfall/
├── AGENTS.md
├── README.md                          # what a waterfall is (two paragraphs), the oracle rule, how to run/deploy
├── pyproject.toml                     # package `waterfall`; deps: fastapi, uvicorn, pydantic, openpyxl, numpy; dev: pytest, httpx
├── waterfall/
│   ├── cashflows.py                   # PRE-BUILT: CashFlow, Contribution/Distribution series; CSV loader
│   ├── irr.py                         # PRE-BUILT: XIRR (Newton + bisection fallback), NPV
│   ├── tiers.py                       # story: Tier, TierKind (ROC | PREF | HURDLE_IRR | HURDLE_MOIC | RESIDUAL), split spec
│   ├── engine.py                      # story: run(cashflows, structure) → per-period, per-tier, per-partner allocations
│   ├── catchup.py                     # story: full / partial catch-up
│   ├── structure.py                   # story: pydantic model of the deal structure (LP/GP %, pref %, compounding, hurdles)
│   ├── report.py                      # story: summary (MOIC, IRR by partner, promote $), CSV/JSON out
│   └── api/
│       ├── main.py                    # story: FastAPI — POST /compute, POST /compute/sensitivity, GET /healthz, GET /version
│       └── schemas.py
├── models/
│   ├── waterfall_reference.xlsx       # THE ORACLE: Inputs, CashFlows, Waterfall, Scenarios tabs; formulas live; ~10 scenarios
│   └── README.md                      # which cells are inputs, which named ranges tests read (out_lp_irr, out_gp_promote, …)
├── tests/
│   ├── conftest.py                    # opens the workbook once with openpyxl (data_only=True → cached values) and yields scenarios
│   ├── test_irr.py                    # XIRR vs the workbook's =XIRR() cells per scenario
│   ├── test_engine_vs_excel.py        # per scenario: engine distributions == 'Waterfall' tab rows, 0.005 per cell
│   ├── test_tiers.py                  # unit tests per tier kind on tiny hand-made flows
│   ├── test_catchup.py
│   ├── test_api.py                    # httpx against the app; /compute on the sample deal returns the workbook's outputs
│   └── test_invariants.py             # hypothesis: sum of partner distributions == total distributions; no tier over-allocates
├── deploy/
│   ├── waterfall.service              # systemd unit template (uvicorn, port from env, WorkingDirectory=release path)
│   └── deploy.sh                      # what the factory deployment script runs: venv, install, migrate nothing, restart, curl /healthz
├── sample/
│   ├── deal_cashflows.csv             # the workbook's CashFlows tab as CSV
│   └── deal_structure.json            # the workbook's Inputs tab as JSON
└── docs/prd/                          # the factory writes the PRD into Build Mill; this folder holds diagrams if any
```

**Important:** the workbook's cached values must be up to date (open, recalc, save in Excel) because tests read `data_only=True` cached results — openpyxl does not evaluate formulas. `models/README.md` says so, and `conftest.py` fails loudly if a referenced cell is `None`.

Pre-built before the meeting: `cashflows.py`, `irr.py`, the oracle workbook with ~10 scenario tabs (European vs American waterfall, 8%/10% pref, compounding vs simple, with/without catch-up, two and three IRR hurdles), `deploy/`, `sample/`, the test *scaffolding* (`conftest.py`, `test_irr.py` green). The **feature** and its stories are what the factory builds; merge stories 1–4 before the meeting and cut one release end-to-end so the release page has a promoted build. Size ≈ 1 MB.

## 4. Agent Instructions (paste-ready)

```markdown
## Project overview
A distribution-waterfall engine for real-estate equity deals (return of capital, preferred return, IRR/MOIC hurdles, catch-up, promote splits between LP and GP) with a small FastAPI service. The firm's Excel model in `models/waterfall_reference.xlsx` is the oracle: tests read expected values from its cells and the engine must match them to the penny on every scenario tab. Work arrives as a feature with an approved PRD and child stories; read the PRD and the sibling stories' plans in the run context before designing.

## Tech stack
Python 3.12; `numpy` for arithmetic, `pydantic` v2 for the deal-structure model, `openpyxl` (read-only, `data_only=True`) for the oracle, FastAPI + uvicorn for the API; tests with `pytest`, `httpx`, `hypothesis`. Deployed as a systemd service by the factory's deployment (see `deploy/`).

## Commands
- `pip install -e .[dev]`
- `python -m pytest -q` — whole suite (< 30 s)
- `python -m pytest tests/test_engine_vs_excel.py -q -k <scenario>` — one scenario tab
- `uvicorn waterfall.api.main:app --port 8080` — run the API locally
- `curl -s localhost:8080/healthz` — must return `{"status":"ok","version":"<git sha>"}`

## Run commands
Before you submit: `python -m pytest -q` green. If your story touches the engine, every scenario in `test_engine_vs_excel.py` must pass — never mark a scenario xfail to get green; if you believe the workbook is wrong, stop and raise a clarification with the cell reference and your number. Report command, exit code and counts through the pre-submit gate.

## Testing expectations
The workbook is the specification. Each scenario tab defines inputs (named ranges on `Inputs`), a cash-flow series (`CashFlows`), and outputs (`Waterfall` rows per period and per tier; summary cells `out_lp_irr`, `out_gp_irr`, `out_gp_promote`, `out_lp_moic`). Tests compare per-cell within 0.005 for money and 1e-6 for rates. Add unit tests for any new tier kind on hand-made 3–5 period flows where you can compute the answer by hand. Property tests must keep holding: partners' distributions sum to total distributions; a tier never allocates more than what reaches it; IRR is monotone in distributions.

## Environment setup
Local: none. Deployed: `WATERFALL_PORT` (plain) and `WATERFALL_ENV` (`uat` | `production`, plain) are provided by the deployment's env vars; the service reads them and exposes `WATERFALL_ENV` on `/version`. No secrets exist in this project.

## Things to avoid
- Don't implement IRR with a fixed-iteration Newton and no fallback; `irr.py` already has a bracketing fallback — use it.
- Don't reorder tiers or apply a hurdle before return of capital; the structure model defines order explicitly.
- Don't round inside the engine; round only in `report.py` and the API responses.
- Don't edit `models/waterfall_reference.xlsx`. If a story needs a new scenario, ask the manager to add the tab; the tests discover tabs automatically.
- Don't change `/healthz`'s shape — the deployment health check depends on it.

## Permissions or boundaries
Work under `waterfall/`, `tests/`, and `sample/`. Ask via clarification before touching `deploy/` or `pyproject.toml` dependencies. Never add or modify anything under `models/` — the oracle workbook is the manager's to change — and never commit a `.env`.
```

## 5. Project settings

- **Pre-submit gate command:** `python -m pytest -q`
- **Project environment:** none required for runs.
- **Automated suite:** optional — `layer: api`, run command `python -m pytest tests/test_api.py -q --junitxml=reports/junit.xml` against the UAT URL via `WATERFALL_BASE_URL` (plain env on the suite), `results_path: reports/junit.xml`, `run_on_uat: true`, `blocks_signoff: false` at first. Nice to have; the manual test cases from approved test plans carry the sign-off story on their own.
- **Deployments (two targets on the same registered server):**
  - *UAT* — branch: release branch, target folder `/opt/demo-waterfall-uat`, script = `deploy/deploy.sh` (creates/updates venv, `pip install -e .`, writes the systemd unit with `WATERFALL_PORT=8081 WATERFALL_ENV=uat`, `systemctl restart waterfall-uat`), health check `http://127.0.0.1:8081/healthz` expecting 200, initial delay 5 s, window 60 s; deployment env vars `WATERFALL_PORT=8081`, `WATERFALL_ENV=uat`.
  - *Production* — same, folder `/opt/demo-waterfall`, port `8080`, `WATERFALL_ENV=production`.
  - Release strategy: releases mode (the factory sets `SF_RELEASE_PATH` / `SF_TARGET`; `deploy.sh` symlinks `current` → release path). Auto-rollback on failed health check is built in — a good thing to *cause* once in rehearsal so you can describe it.
- **Gates:** `auto_approve_*` **off**. Sign-off requires the UAT deploy succeeded and every test case passed — leave one case *blocked* in rehearsal to see it refuse.
- **Version scheme:** default `YYYY.MM.DD.N` — nothing in Agent Instructions overrides it. (Optional: add a `## Versioning & Release` section proposing `v<major>.<minor>` and let the release agent propose; the manager overrides at cut. Only if you want to show that beat.)

## 6. Story backlog

One **feature** whose PRD the factory drafts and the manager approves; the breakdown produces the stories below (write them as the intended outcome so you can compare with what the breakdown agent proposes — approve, or send it back to match). Merge 1–4 and cut/promote one release before the meeting; keep 5–7 in `ready`.

| # | Type | Title | Acceptance criterion |
|---|---|---|---|
| F | **feature** | **Distribution waterfall engine and API, matching `models/waterfall_reference.xlsx`** | PRD approved with: problem (the workbook is the only source of truth and can't be called from anything), goals (engine + API that reproduce every scenario tab to the penny), out of scope (fund-level fees, tax, multi-currency, a UI), acceptance (all scenario tabs pass; `/compute` returns the workbook's summary cells for the sample deal; deployed to UAT with `/healthz`). Breakdown yields stories 1–7. |
| 1 | story | Deal-structure model and tier definitions | `structure.py` / `tiers.py`: LP/GP %, pref rate + compounding, ordered tiers (ROC, PREF, HURDLE_IRR/MOIC with split, RESIDUAL); loads `sample/deal_structure.json`; validation errors name the field; unit tests. |
| 2 | story | Engine: return of capital and preferred return (European) | Scenarios `Euro_8_simple`, `Euro_8_compound`, `Euro_10_compound` pass `test_engine_vs_excel.py`; property tests hold. |
| 3 | story | Engine: IRR hurdles with promote splits | Scenarios `Hurdle_2tier`, `Hurdle_3tier` pass; hurdle attainment solved per period with the XIRR in `irr.py`; over-allocation invariant holds. |
| 4 | story | Catch-up (full and partial) | Scenarios `Catchup_full`, `Catchup_50` pass; catch-up percentage is a structure field with validation. |
| 5 | story | FastAPI `/compute` and `/healthz` | `POST /compute` with `sample/deal_structure.json` + `sample/deal_cashflows.csv` returns `out_lp_irr`, `out_gp_irr`, `out_gp_promote`, `out_lp_moic` equal to the workbook; `/healthz` returns status + git sha; `/version` returns `WATERFALL_ENV`; `test_api.py` green. |
| 6 | story | American (deal-by-deal) waterfall option | Scenarios `American_8`, `American_hurdle` pass; structure gains a `style` field (`european` or `american`); docs updated. |
| 7 | story | Sensitivity endpoint | `POST /compute/sensitivity` varies exit value and hold period over a grid and returns LP IRR / GP promote per cell; result for the sample deal's base cell equals `/compute`; response under 2 s for a 10×10 grid. |
| 8 | **bug** *(optional, seeded)* | Pref return over-accrues in a month with two contributions | RCA identifies day-count handling in `engine.py`; fix; the affected scenario tab (add `Pref_two_contribs` to the workbook first) passes. |

Not the live demo (that's #3); this project is shown for the PRD gate and the release page.

## 7. Live demo script

1. Open the feature: the PRD (problem, goals, out of scope, acceptance) with its **approval** — "engineering did not start until a human approved this." Show the breakdown that produced the stories.
2. Open the merged PR for story 3 (hurdles). Open `tests/test_engine_vs_excel.py`: it opens *the workbook*, iterates scenario tabs, compares cell by cell. "Your spreadsheet is the test suite. When you bring us yours, the tests change; the engine has to keep up."
3. Open the release page: the promoted release — pinned commit, notes written by the release agent from the real commit range, UAT deployment green with its health check, test cases all passed, signed off by name at a timestamp, promoted to Production, **same commit hash on both** — and the audit entries for each step.
4. Show the release is immutable: "if UAT had failed, we'd reject and cut a new one; a version name means one build, forever."
5. Dispatch story 5 (the API). When the plan returns, **reject once**: "Version the response — add `/version` and include the git sha in `/healthz` so the deployment health check proves *which* build is up." Retry carries it.
6. If time: cut a release live from `main` (it queues; the release agent writes notes and deploys UAT while you talk), then come back to it after #5's eval demo to show UAT deployed and the test cases waiting for a human.
7. Close on the deployment's rollback rule: "a failed health check rolls the target back automatically; the release goes to rejected; nothing reaches Production without a signature."

## 8. Security / credentials answer

Nothing in this project touches the firm's data at all: the only input is the workbook, checked into the repo, and the service computes on posted cash flows. The deployment's SSH credential is held write-only in the factory's private bucket, used by the factory's own process to push the pinned build, and is never available to an agent run.

## 9. Prep & risks

- Runner box: Python 3.12; the VM needs Python 3.12 + systemd units for `waterfall-uat` / `waterfall` (create once by hand; `deploy.sh` restarts them). Ports 8080/8081 must be free.
- **Recalculate and save the workbook in Excel** before committing — openpyxl reads cached values; a workbook saved by a library has none and every test errors. `conftest.py` should fail with a clear message on `None`.
- Build the workbook honestly: real formulas (`XIRR`, cumulative pref with compounding, hurdle checks), scenario tabs driven by `Inputs` — a finance person in the room may open it.
- Cut, UAT-deploy, test, sign off and promote one release the day before; note how long the release agent takes so the live cut in step 6 doesn't surprise you.
- Rehearse a failed health check once (point the UAT unit at the wrong port) to see the auto-rollback and to know how the release page reports it.
- Keep the PRD short; the breakdown agent turns a long PRD into too many stories.
