-- 286_the_catalog_carries_five_cre_demo_templates (us-118.5): the platform
-- catalog gains five templates, each a copy of the Default template with the
-- Agent Instructions document and every per-task instruction file rewritten
-- for one of the CRE demo projects in docs/demos/cre/.
--
-- What "a copy of Default" means here is exactly what the admin page's
-- Duplicate does (apps/api/app/routers/admin.py, duplicate_project_template):
-- the row's face plus its `worker_instruction` sections — the retired
-- guideline and prompt rows are rollback data on the source, not content a
-- new template inherits.
--
-- Data only; migration 284 holds the schema. Idempotent and conservative:
--   * a template is inserted only if its key is free — an admin who has
--     already made or edited one of these keeps their version;
--   * sections are copied only for a row this migration created;
--   * covers are the built-ins added in the same change
--     (apps/web/public/template-covers/*.svg), so this runs the same on both
--     projects with no Storage upload;
--   * nothing here touches an org's copies — a manager adds a catalog
--     template to their org from Settings → Project templates.
--
-- The `agent_instructions` bodies are the "Agent Instructions (paste-ready)"
-- blocks of docs/demos/cre/0N-*.md, verbatim, followed by the Default
-- template's own document (Working with Build Mill, Versioning & Release) so
-- a project created from one of these still knows how the factory works.
-- Each per-task file is the Default's text with one project-specific
-- paragraph in front of it.

do $mig$
declare
  v_default_id  uuid;
  v_default_doc text;
  v_id          uuid;
  v_key         text;
  v_addenda     jsonb;
  v_made        int := 0;
begin
  select id, coalesce(agent_instructions, '')
    into v_default_id, v_default_doc
    from public.project_templates
   where key = 'default';
  if v_default_id is null then
    raise notice '286: no default template — nothing to seed';
    return;
  end if;

  -- ------------------------------------------------------------ 1. CRE · Legacy report conversion (dbt)
  v_key := 'cre-report-conversion';
  v_id := null;
  insert into public.project_templates
    (key, name, description, category, image_path, sort_order, agent_instructions)
  values
    (v_key,
     'CRE · Legacy report conversion (dbt)',
     $desc01$Convert SSRS/Excel/Access reports into **dbt** models one story at a time, each with schema tests and a to-the-penny reconciliation against the legacy output. Ships with a synthetic CRE portfolio in **DuckDB**, so no warehouse credential is needed to build or test.$desc01$,
     'Data platform',
     'builtin/data-pipeline',
     10,
     $doc01$## Project overview
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
Work only in `models/marts/`, `tests/`, `docs/conversion-notes/`, and the mart's `schema.yml`. Ask (via clarification) before adding a new seed or macro. Never add credentials or a `.env` file to the repo.$doc01$
       || E'\n\n' || v_default_doc)
  on conflict (key) do nothing
  returning id into v_id;

  if v_id is not null then
    v_addenda := jsonb_build_object(
      'prd', $k01prd$This is a dbt project that replaces a legacy reporting estate. A feature here is a batch of report conversions or a shared reconciliation capability: name the legacy reports (the files under `legacy/`), state the reconciliation rule (each mart matches `legacy/expected/<report>.csv` to the penny; a documented legacy defect is written down as a variance, never matched), and say which planted anomalies in `data/README.md` are in scope. Editing `legacy/` or `data/` is always out of scope.$k01prd$,
      'plan', $k01pla$A conversion story's plan names: the legacy source in `legacy/` you read and what its query or formulas actually compute; the mart's grain and measures; which staging and intermediate models it reuses (never recompute NOI from the GL when `int_noi_monthly` exists); the reconciliation test against `legacy_expected.<report>`; and any variance you expect from a documented anomaly. Test-plan cases here look like: 'dbt build passes', 'the mart reconciles to the legacy CSV within 0.005 on its grain', 'the conversion note lists every variance'.$k01pla$,
      'code', $k01cod$Add the mart under `models/marts/`, its `schema.yml` tests, `tests/reconcile_<report>.sql`, and `docs/conversion-notes/<report>.md`. Do not edit `legacy/`, `data/`, staging or intermediate models. If you can run dbt, run `dbt deps && dbt seed && dbt build` before submitting and report the evidence; a reconciliation that fails on a planted anomaly is excluded row by row with a comment naming the anomaly — never loosened, and never 'fixed' by matching the wrong number.$k01cod$,
      'release', $k01rel$For this dbt project, the notes name each mart added or changed and its reconciliation status (matched, or documented variance); the test cases are 'run dbt build' plus a spot check per mart against `legacy/expected/`.$k01rel$,
      'breakdown', $k01bre$Split by report: one story per legacy report, one mart each, ordered by what staging or intermediate models they need. A defect found in the legacy report itself becomes a bug work item, not a silent fix inside a conversion story.$k01bre$,
      'test', $k01tes$Execute with `dbt deps && dbt seed && dbt build` on the branch; per case, compare the mart to `legacy_expected.<report>` on its grain and read the conversion note for the variances it claims.$k01tes$,
      'deploy', $k01dep$This project has no deployment target of its own — a deploy run here should release_work with a note saying so rather than triggering anything.$k01dep$,
      'guidelines', $k01gui$The commands are dbt commands (`dbt deps`, `dbt seed`, `dbt build`, `dbt build --select <model>+`) — ground them in `dbt_project.yml`, `packages.yml` and `requirements.txt`. Keep the reconciliation rule and the 'never edit `legacy/` or `data/`' boundary intact in any rewrite.$k01gui$,
      'elaborate', $k01ela$Name the actual `.rdl` or `.xlsx` under `legacy/`, the expected CSV under `legacy/expected/`, and the staging models the mart will read; the acceptance criterion is a grain and a tolerance, not an impression.$k01ela$,
      'wireframe', $k01wir$This project has no user interface. Hand back no_ui_surface naming the mart the story adds.$k01wir$
    );
    insert into public.project_template_sections
      (template_id, section_type, section_key, title, content, sort_order)
    select v_id, 'worker_instruction', s.section_key, s.title,
           coalesce((v_addenda ->> s.section_key) || E'\n\n', '') || s.content,
           s.sort_order
      from public.project_template_sections s
     where s.template_id = v_default_id
       and s.section_type = 'worker_instruction';
    v_made := v_made + 1;
  else
    raise notice '286: % already exists — left alone', v_key;
  end if;

  -- ------------------------------------------------------------ 2. CRE · Power BI semantic model as code
  v_key := 'cre-power-bi-model';
  v_id := null;
  insert into public.project_templates
    (key, name, description, category, image_path, sort_order, agent_instructions)
  values
    (v_key,
     'CRE · Power BI semantic model as code',
     $desc02$A Power BI Project (**PBIP/TMDL**) whose semantic model is text in git; stories add measures, fix relationships and standardize the model, each reviewed as a **DAX diff** with a lint gate. Vendored CRE data — no gateway, workspace or warehouse credential.$desc02$,
     'Data platform',
     'builtin/bi-model',
     11,
     $doc02$## Project overview
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
Edit only under `CRE Portfolio.SemanticModel/definition/`, `tests/measures_expected.yaml`, and `tools/lint_allowlist.yaml` (removals only). Ask via clarification before changing `expressions.tmdl` or `model.tmdl`. Never modify the `.Report/` folder unless the story is about the report.$doc02$
       || E'\n\n' || v_default_doc)
  on conflict (key) do nothing
  returning id into v_id;

  if v_id is not null then
    v_addenda := jsonb_build_object(
      'prd', $k02prd$This is a Power BI semantic model held as TMDL text. A feature here is a change to the model — a family of measures, a relationship fix, a convention applied across the model: name the measures and relationships, the format strings and description requirements, and the naming convention in `tools/conventions.md`. The report pages and publishing to a workspace are out of scope unless the feature is about them.$k02prd$,
      'plan', $k02pla$Name the `.tmdl` files touched (`tables/_Measures.tmdl`, `relationships.tmdl`, a table's partition), the DAX shape (`DIVIDE` over `/`, `CALCULATE` with explicit filters, time intelligence over the marked `Date` table), the format string and one-sentence description, and the `tests/measures_expected.yaml` row. Test-plan cases are lint-shaped: 'the TMDL lint passes', 'the measure has a description and a format string', 'no many-to-many relationship remains'.$k02pla$,
      'code', $k02cod$Edit TMDL by copying shapes that already exist in the repo — indentation, a fresh `lineageTag` on every new object, measures only in `_Measures`. Add the expected row for any new measure; never grow `tools/lint_allowlist.yaml`. DAX cannot execute in this environment: write it plainly and say so in your hand-back notes. Run `python -m pytest -q` if you can and report the evidence.$k02cod$,
      'release', $k02rel$For this model, the notes list measures added or renamed (old → new) and relationships changed; the test cases are 'open the PBIP in Power BI Desktop, refresh, verify the named measures'.$k02rel$,
      'breakdown', $k02bre$One story per measure family or relationship fix; a bulk rename or reformat across the model is a chore, not a story.$k02bre$,
      'test', $k02tes$Run `python -m pytest -q` on the branch; per case, open the model in Power BI Desktop where one is available and check the measure — otherwise mark the case blocked with 'no Desktop' as the evidence.$k02tes$,
      'deploy', $k02dep$This project has no deployment target — a deploy run here should release_work with a note saying so.$k02dep$,
      'guidelines', $k02gui$The commands are `python -m pytest -q` and `python tools/tmdl_check.py --explain`; the naming convention is `tools/conventions.md` and stays authoritative. Keep the 'copy TMDL shapes that exist, never invent syntax' rule.$k02gui$,
      'elaborate', $k02ela$Name the actual table and measure names from the TMDL and the convention rule that applies; one acceptance criterion per measure.$k02ela$,
      'wireframe', $k02wir$This project has no user interface unless the story is about the report page. Hand back no_ui_surface naming the measure or relationship the story changes.$k02wir$
    );
    insert into public.project_template_sections
      (template_id, section_type, section_key, title, content, sort_order)
    select v_id, 'worker_instruction', s.section_key, s.title,
           coalesce((v_addenda ->> s.section_key) || E'\n\n', '') || s.content,
           s.sort_order
      from public.project_template_sections s
     where s.template_id = v_default_id
       and s.section_type = 'worker_instruction';
    v_made := v_made + 1;
  else
    raise notice '286: % already exists — left alone', v_key;
  end if;

  -- ------------------------------------------------------------ 3. CRE · Rent roll & T-12 normalizer
  v_key := 'cre-rent-roll-normalizer';
  v_id := null;
  insert into public.project_templates
    (key, name, description, category, image_path, sort_order, agent_instructions)
  values
    (v_key,
     'CRE · Rent roll & T-12 normalizer',
     $desc03$A Python package that turns any broker or PM-system rent roll (and T-12) workbook into **one canonical schema** — one parser per format, each pinned by a fixture and a golden CSV. A new format is a story; no credentials, no network.$desc03$,
     'Data platform',
     'builtin/spreadsheet',
     12,
     $doc03$## Project overview
A Python package that normalizes commercial-real-estate rent rolls and T-12 operating statements from many Excel layouts (brokers, Yardi/MRI/RealPage exports, Argus) into one canonical schema. Each layout is a parser under `rentroll/parsers/`, pinned by a fixture workbook and a golden CSV. Work items are almost always "add a parser for this workbook" — the workbook is attached to the work item as a document; copy it into `fixtures/<format>/` unchanged.

## Tech stack
Python 3.12; `openpyxl` (read cells, merged ranges, formulas as values), `pandas` for shaping, `pydantic` v2 for the canonical schema, `typer` CLI; tests with `pytest` + `hypothesis`. No network, no database, no credentials.

## Commands
- `pip install -e .[dev]`
- `python -m pytest -q` — whole suite (< 20 s)
- `rentroll parse fixtures/<format>/<file>.xlsx --format <name> --out /tmp/out.csv`
- `rentroll validate /tmp/out.csv` — schema + tie-out checks on any canonical CSV
- `python tools/make_fixtures.py` — regenerate the synthetic fixtures (only if a story says so)

## Run commands
Before you submit: `python -m pytest -q` green, including the new (fixture, expected) pair your parser adds — the golden test and the totals-tie test are parametrized over whatever is on disk, so a new fixture with no expected CSV fails loudly. Produce the golden CSV with your parser, then *read the workbook's own total row and confirm the tie by hand* before committing it. Report command, exit code and counts through the pre-submit gate.

## Testing expectations
A parser is done when: (1) its golden CSV matches exactly on identifiers and text and within 0.005 on money; (2) `sum(base_rent_monthly)` equals the sheet's stated total (and per-floor/per-building subtotals where the sheet has them); (3) required columns are non-null for occupied units, vacant units are rows with `tenant_name = null, status = vacant`, month-to-month leases have `lease_end = null, status = mtm`; (4) an unknown or missing column raises `ParseError` naming the header — never silently mis-map. Match columns by **header text** (normalized: lowercase, stripped, synonyms in `base.py`), never by position. Annual rents are converted to monthly and the decision is recorded in `docs/formats/<format>.md`.

## Environment setup
None. There are no environment variables and there must be no `.env` file.

## Things to avoid
- Don't match columns by index or by letter — layouts drift; headers are the contract.
- Don't hardcode sheet names; find the sheet by a header signature (`base.py` has helpers).
- Don't "clean" a fixture workbook — it must stay exactly as received; the parser adapts to it.
- Don't reuse an existing parser by copy-paste when a subclass with overridden `header_map` will do; but don't force a subclass when the layout is genuinely different either.
- Don't compute rent PSF from `sf` when the sheet gives it — parse it and let `validate.py` cross-check.
- Don't drop rows you don't understand. Raise, or emit them with `status = unknown` and a note, and say which in the PR.

## Permissions or boundaries
Add files under `rentroll/parsers/`, `fixtures/<new format>/`, `expected/<new format>/`, `docs/formats/`; edit `registry.py` to register. Ask via clarification before changing `schema.py`, `base.py` or `normalize.py` — those are shared by every parser. Never modify an existing fixture or golden CSV unless the story is about that format.$doc03$
       || E'\n\n' || v_default_doc)
  on conflict (key) do nothing
  returning id into v_id;

  if v_id is not null then
    v_addenda := jsonb_build_object(
      'prd', $k03prd$This is a parser-per-format package. A feature here is a family of formats or a capability (format detection, an export, a CLI): name the formats, the canonical schema fields affected, and the tie-out rule (parsed totals equal the sheet's own total row). Changing an existing fixture workbook is out of scope.$k03prd$,
      'plan', $k03pla$A parser story's plan names: the workbook (an attached document that lands in `fixtures/<format>/` unchanged), the header signature that identifies its sheet, the mapping to the canonical schema by header text — never by column position — how totals and subtotals are tied out, and how an unknown column fails. Test-plan cases: 'the golden CSV matches', 'sum(base_rent_monthly) ties to the sheet total', 'an unknown header raises ParseError'.$k03pla$,
      'code', $k03cod$Add `rentroll/parsers/<format>.py`, register it in `registry.py`, copy the attached workbook into `fixtures/<format>/` unchanged, produce `expected/<format>/*.csv` with your parser and check the tie against the sheet's total row by hand, and write `docs/formats/<format>.md`. Do not touch `schema.py`, `base.py` or `normalize.py` without a clarification. Run `python -m pytest -q` if you can and report the evidence.$k03cod$,
      'release', $k03rel$For this package, the notes list the formats added; the test cases are 'parse the fixture with `rentroll parse` and compare the totals'.$k03rel$,
      'breakdown', $k03bre$One story per format; detection, the CLI and any export are their own stories.$k03bre$,
      'test', $k03tes$Run `python -m pytest -q` on the branch; per case, run `rentroll parse` on the fixture and check the totals against the sheet.$k03tes$,
      'deploy', $k03dep$This project has no deployment target — a deploy run here should release_work with a note saying so.$k03dep$,
      'guidelines', $k03gui$The commands are `pip install -e .[dev]`, `python -m pytest -q`, `rentroll parse` and `rentroll validate`; the header-not-position rule and the fixtures-are-immutable rule stay in any rewrite.$k03gui$,
      'elaborate', $k03ela$Name the attached workbook, its sheet or sheets, where the total row sits, and the required columns; the acceptance criterion is a tie-out, not a description.$k03ela$,
      'wireframe', $k03wir$This project has no user interface unless the story adds the CLI or a UI. Hand back no_ui_surface naming the parser the story adds.$k03wir$
    );
    insert into public.project_template_sections
      (template_id, section_type, section_key, title, content, sort_order)
    select v_id, 'worker_instruction', s.section_key, s.title,
           coalesce((v_addenda ->> s.section_key) || E'\n\n', '') || s.content,
           s.sort_order
      from public.project_template_sections s
     where s.template_id = v_default_id
       and s.section_type = 'worker_instruction';
    v_made := v_made + 1;
  else
    raise notice '286: % already exists — left alone', v_key;
  end if;

  -- ------------------------------------------------------------ 4. CRE · Waterfall & promote calculator
  v_key := 'cre-waterfall-calculator';
  v_id := null;
  insert into public.project_templates
    (key, name, description, category, image_path, sort_order, agent_instructions)
  values
    (v_key,
     'CRE · Waterfall & promote calculator',
     $desc04$A distribution-waterfall engine with a small **FastAPI** service whose tests read expected values from **the firm's own Excel model**. Deployed to UAT and Production through the release path, so a release shows the same pinned commit from cut to promote.$desc04$,
     'CRE applications',
     'builtin/finance-model',
     13,
     $doc04$## Project overview
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
Work under `waterfall/`, `tests/`, and `sample/`. Ask via clarification before touching `deploy/` or `pyproject.toml` dependencies. Never add or modify anything under `models/` — the oracle workbook is the manager's to change — and never commit a `.env`.$doc04$
       || E'\n\n' || v_default_doc)
  on conflict (key) do nothing
  returning id into v_id;

  if v_id is not null then
    v_addenda := jsonb_build_object(
      'prd', $k04prd$This is a waterfall engine and API whose specification is the Excel model in `models/waterfall_reference.xlsx`: name the scenario tabs the feature must reproduce, the tier kinds (return of capital, preferred return, IRR/MOIC hurdles, catch-up, promote splits) and the API endpoints. Fund-level fees, tax, multi-currency and a UI are out of scope unless the feature names them.$k04prd$,
      'plan', $k04pla$Name the tier kinds and structure fields, the scenario tabs that must pass in `test_engine_vs_excel.py`, the invariants (partners' distributions sum to the total; no tier over-allocates), and for API stories the endpoints and the `/healthz` contract. Test-plan cases: 'scenario <tab> matches within 0.005', 'POST /compute returns the workbook's summary cells for the sample deal', 'GET /healthz returns status and the git sha'.$k04pla$,
      'code', $k04cod$Never edit `models/waterfall_reference.xlsx`, and never mark a scenario xfail — if you believe the workbook is wrong, raise a clarification with the cell reference and your number. Round only in `report.py` and the API responses; keep the `/healthz` shape the deployment health check depends on. Run `python -m pytest -q` if you can and report the evidence.$k04cod$,
      'release', $k04rel$For this service, the notes name scenarios newly passing and endpoints added; the test cases include `GET /healthz` and `GET /version` on the UAT deployment and one `POST /compute` against the sample deal compared to the workbook.$k04rel$,
      'breakdown', $k04bre$Split along the engine: structure model, return of capital and preferred return, IRR hurdles, catch-up, the API, then the American variant and sensitivity — each story names the scenario tabs it makes pass.$k04bre$,
      'test', $k04tes$Run `python -m pytest -q` on the branch; exercise API cases against a local `uvicorn` or the UAT URL in your context.$k04tes$,
      'deploy', $k04dep$This project deploys through `deploy/deploy.sh`; the health check is `GET /healthz`, which must return 200 with the git sha, and `GET /version` must report the target's `WATERFALL_ENV`. Verify both before declaring the deployment healthy.$k04dep$,
      'guidelines', $k04gui$The commands are `python -m pytest -q` and `uvicorn waterfall.api.main:app --port 8080`; the workbook-is-the-oracle rule and the 'never edit `models/`' boundary stay in any rewrite.$k04gui$,
      'elaborate', $k04ela$Name the scenario tabs and named ranges (`out_lp_irr`, `out_gp_promote`, …) the story must satisfy; one acceptance criterion per scenario or endpoint.$k04ela$,
      'wireframe', $k04wir$This is an engine and an API with no user interface unless the story adds one. Hand back no_ui_surface naming the tier or endpoint the story adds.$k04wir$
    );
    insert into public.project_template_sections
      (template_id, section_type, section_key, title, content, sort_order)
    select v_id, 'worker_instruction', s.section_key, s.title,
           coalesce((v_addenda ->> s.section_key) || E'\n\n', '') || s.content,
           s.sort_order
      from public.project_template_sections s
     where s.template_id = v_default_id
       and s.section_type = 'worker_instruction';
    v_made := v_made + 1;
  else
    raise notice '286: % already exists — left alone', v_key;
  end if;

  -- ------------------------------------------------------------ 5. CRE · Text-to-SQL agent with evals
  v_key := 'cre-text-to-sql-evals';
  v_id := null;
  insert into public.project_templates
    (key, name, description, category, image_path, sort_order, agent_instructions)
  values
    (v_key,
     'CRE · Text-to-SQL agent with evals',
     $desc05$A schema-aware **text-to-SQL** agent over a CRE portfolio warehouse (DuckDB in-repo), read-only, with an **eval harness** where every business question is a case with a known answer; the harness emits JUnit and runs as a factory suite. Every new question is a story.$desc05$,
     'AI systems',
     'builtin/assistant',
     14,
     $doc05$## Project overview
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
Add cases under `evals/cases/`, edit `warehouse_qa/schema_notes.md` and `docs/decisions/`; ask via clarification before changing `agent.py`, `guard.py`, `compare.py`, or `evals/run.py`. Never touch `data/` or the committed baseline summary.$doc05$
       || E'\n\n' || v_default_doc)
  on conflict (key) do nothing
  returning id into v_id;

  if v_id is not null then
    v_addenda := jsonb_build_object(
      'prd', $k05prd$This is a text-to-SQL assistant with an eval harness. A feature here is a family of questions or a capability of the assistant (guardrails, the cost report, an MCP server): name the questions as cases and the business definitions to pin. Any warehouse other than the in-repo DuckDB, and any write access, is out of scope.$k05prd$,
      'plan', $k05pla$Name the case id and question, the oracle SQL's grain, the tables in `must_use_tables` and `must_not_touch`, and the `schema_notes.md` definition you expect to add — the fix for a wrong answer is a definition, not a code path. Test-plan cases: 'the case passes 3 of 3 runs', 'every previous case still passes', 'the guard rejects a non-SELECT'.$k05pla$,
      'code', $k05cod$Add `evals/cases/<id>.yaml` with a hand-written oracle SQL, edit `warehouse_qa/schema_notes.md`, and add a `docs/decisions/` page if a definition was pinned. Do not touch `agent.py`, `guard.py`, `compare.py` or `evals/run.py` without a clarification, and never edit an oracle to match the assistant. If `ANTHROPIC_API_KEY` is in your environment, run `python -m pytest -q && python -m evals.run` and report the evidence; if it is not, say so via clarification rather than skipping the eval.$k05cod$,
      'release', $k05rel$For this assistant, the notes list cases added and any definition changes; the eval suite runs on the release itself, so do not describe its result.$k05rel$,
      'breakdown', $k05bre$One story per question (one case each); guardrails, the cost column and the MCP server are separate stories.$k05bre$,
      'test', $k05tes$Run `python -m pytest -q` and, with `ANTHROPIC_API_KEY` in your environment, `python -m evals.run`; per case, the summary table in `reports/summary.md` is the evidence.$k05tes$,
      'deploy', $k05dep$This project has no deployment target by default — a deploy run here should release_work with a note saying so.$k05dep$,
      'guidelines', $k05gui$The commands are `python -m pytest -q`, `python -m evals.run` and `wqa ask`; the 'definitions, not code paths' rule and the SELECT-only guard stay in any rewrite.$k05gui$,
      'elaborate', $k05ela$Name the case id, the tables and the definition involved; make the acceptance criterion the tolerance and the repetition count.$k05ela$,
      'wireframe', $k05wir$This project has no user interface unless the story adds one. Hand back no_ui_surface naming the case the story adds.$k05wir$
    );
    insert into public.project_template_sections
      (template_id, section_type, section_key, title, content, sort_order)
    select v_id, 'worker_instruction', s.section_key, s.title,
           coalesce((v_addenda ->> s.section_key) || E'\n\n', '') || s.content,
           s.sort_order
      from public.project_template_sections s
     where s.template_id = v_default_id
       and s.section_type = 'worker_instruction';
    v_made := v_made + 1;
  else
    raise notice '286: % already exists — left alone', v_key;
  end if;

  raise notice '286: created % of 5 CRE demo templates', v_made;
end $mig$;
