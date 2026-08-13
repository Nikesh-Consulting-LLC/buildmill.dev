# Dispatch PRD Drafting Through the Worker Pool — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make "Draft PRD" create a `kind = 'prd'` run in the existing worker pool instead of calling the LLM synchronously inside the request, so any active worker (the runner by default, or a human's connected agent) can claim, draft, and submit it back through the same claim/context/submit contract plan and code runs already use.

**Architecture:** A new SQL function `dispatch_prd_draft(p_issue)` inserts a `queued`, `kind='prd'` run (title/body/guidelines/learnings/prior-draft/feedback in `input_context`, no repo/branch fields). `POST /issues/{id}/prd/draft` calls it via RPC instead of calling the LLM inline. The worker pool (`/worker/*`), the runner, both providers, and the factory MCP server gain a `prd` branch alongside their existing `plan`/`code` handling — the `prd` branch skips every git/repo step. `perform_submit`'s `prd` branch writes the `artifacts` row exactly as the old synchronous endpoint did (supersede, version+1, `kind='prd'`, `status='draft'`) and moves the issue to `prd-review`. The frontend's Draft PRD button becomes fire-and-forget with a live queued/in-progress indicator driven by a Realtime subscription on `runs`.

**Tech Stack:** FastAPI + psycopg (apps/api), Postgres/Supabase (RLS + a plpgsql RPC function), a standalone Python runner process (apps/runner) that already speaks the worker-pool REST contract, the FastMCP-based factory MCP server, Next.js 16 App Router + Supabase Realtime (apps/web).

## Global Constraints

- No new `issues.status` value — the enum in `031_issues.sql:56-60` already covers every state this feature needs (`draft`, `prd-review`, `ready`, ...). A `prd`-kind run in flight does **not** change `issues.status`; "queued/in-progress" is derived purely from the existence of an active `prd`-kind row in `runs`.
- `artifacts.kind` already allows `'prd'` (`031_issues.sql:176`) — no artifacts migration needed.
- `runs.status` already allows `'queued'/'running'/'succeeded'/'failed'` (`005_runs.sql:11`) — no change needed; only `runs.kind`'s check constraint needs widening.
- Per the story's explicit out-of-scope line, **wireframe auto-generation stops firing on PRD drafts** in this plan. It was inline synchronous code in the old `draft_prd` (`workflow.py:291-305`) that ran *after* the LLM produced content — there is no content at dispatch time anymore, and wiring wireframe generation into the async completion path (`db.complete_run`, which is sync psycopg with no LLM/document access) is real scope the story marked out. `_generate_prd_wireframes`/`_wants_wireframes`/`_parse_wireframes` in `workflow.py` are left in place (dead code, called from nowhere after Task 5) rather than deleted, since re-wiring them is likely a fast follow-up, not because unused code is normally acceptable — flag this to the user after the plan lands.
- Every new/changed Python file keeps the existing project style: no type-hint-only helper churn, docstrings only where the "why" isn't obvious from the code, HTTPException with actionable `detail` strings (matching the tone already in `worker.py`/`workflow.py`).
- Postgres check-constraint names: `alter table ... add column kind text ... check (...)` without an explicit `constraint <name>` clause auto-names it `<table>_<column>_check` — i.e. `runs_kind_check`. Task 1 verifies this against the live DB before dropping it.

---

### Task 1: Migration — widen `runs.kind` + add `dispatch_prd_draft`

**Files:**
- Create: `infra/supabase/migrations/042_dispatch_prd_draft.sql`

**Interfaces:**
- Produces: SQL function `public.dispatch_prd_draft(p_issue uuid) returns uuid` — callable via PostgREST RPC as `rpc(settings, user.token, "dispatch_prd_draft", {"p_issue": str(issue_id)})`, same pattern as the existing `dispatch_issue` call in `apps/api/app/routers/issues.py:34-36`.
- Produces: `runs.kind` now accepts `'prd'` in addition to `'plan'`/`'code'` — every downstream task in this plan depends on this.

- [ ] **Step 1: Confirm the live constraint name**

Run (via the Supabase MCP `execute_sql` tool or `psql`):
```sql
select conname, pg_get_constraintdef(oid)
from pg_constraint
where conrelid = 'public.runs'::regclass and contype = 'c' and conname like '%kind%';
```
Expected: one row, `conname = runs_kind_check`, def `CHECK ((kind = ANY (ARRAY['plan'::text, 'code'::text])))`. If the name differs, use the real name in Step 2.

- [ ] **Step 2: Write the migration**

```sql
-- 042_dispatch_prd_draft: PRD drafting joins the worker pool (US-3.21).
--
-- runs.kind gains 'prd' — a run with no repo/branch fields, fulfilled by a
-- direct LLM call instead of the Claude Code CLI-against-a-checkout flow
-- plan/code runs use. dispatch_prd_draft mirrors dispatch_issue's shape
-- but is deliberately its own function: PRD dispatch has none of
-- dispatch_issue's plan-vs-code kind resolution, child-count guard, or
-- approved-plan requirement.

alter table public.runs drop constraint runs_kind_check;
alter table public.runs add constraint runs_kind_check
  check (kind in ('plan', 'code', 'prd'));

create or replace function public.dispatch_prd_draft(p_issue uuid)
returns uuid
language plpgsql
as $$
declare
  v_issue public.issues%rowtype;
  v_prior public.artifacts%rowtype;
  v_feedback text;
  v_context jsonb;
  v_run uuid;
begin
  select * into v_issue from public.issues where id = p_issue for update;
  if not found then
    raise exception 'issue not found';
  end if;
  if v_issue.abandoned_at is not null then
    raise exception 'issue is abandoned';
  end if;
  if v_issue.type <> 'feature' then
    raise exception 'PRDs are only for feature issues';
  end if;
  if v_issue.status not in ('draft', 'prd-review', 'ready') then
    raise exception 'cannot draft PRD from status "%"', v_issue.status;
  end if;

  select * into v_prior
  from public.artifacts
  where issue_id = p_issue and kind = 'prd'
  order by version desc limit 1;

  if v_prior.id is not null then
    select a.comment into v_feedback
    from public.approvals a
    where a.issue_id = p_issue and a.gate = 'prd' and a.decision = 'sent-back'
    order by a.created_at desc limit 1;
  end if;

  v_context := jsonb_build_object(
    'title', v_issue.title,
    'type', v_issue.type,
    'story', v_issue.body,
    'body', v_issue.body,
    'run_kind', 'prd',
    'guidelines', public.assemble_project_guidelines(v_issue.project_id),
    'learnings', public.assemble_project_learnings(v_issue.project_id)
  );
  if v_prior.id is not null then
    v_context := v_context || jsonb_build_object('previous_prd', v_prior.content);
  end if;
  if v_feedback is not null then
    v_context := v_context || jsonb_build_object('feedback', v_feedback);
  end if;

  insert into public.runs (org_id, issue_id, provider, status, kind, input_context)
  values (v_issue.org_id, p_issue, 'claude', 'queued', 'prd', v_context)
  returning id into v_run;

  insert into public.issue_events (org_id, issue_id, type, payload)
  values (v_issue.org_id, p_issue, 'prd-dispatched', jsonb_build_object('run_id', v_run));

  return v_run;
end;
$$;
```

- [ ] **Step 3: Apply the migration live**

Use the Supabase MCP `apply_migration` tool with name `042_dispatch_prd_draft` and the SQL above (the project is `Software-Factory` / `wdudmfhhqxrqzoyhuzwx`, per `CLAUDE.md`).

- [ ] **Step 4: Regenerate types**

Use the Supabase MCP `generate_typescript_types` tool and write the result to `apps/web/src/lib/supabase/database.types.ts`.

- [ ] **Step 5: Smoke-test the function directly**

```sql
-- pick any feature issue in status draft/prd-review/ready and confirm it queues:
select public.dispatch_prd_draft('<a real feature issue uuid>');
select kind, status, input_context ? 'guidelines' as has_guidelines
from public.runs where issue_id = '<same uuid>' order by created_at desc limit 1;
```
Expected: `kind = 'prd'`, `status = 'queued'`, `has_guidelines = true`.

- [ ] **Step 6: Commit**

```bash
git add infra/supabase/migrations/042_dispatch_prd_draft.sql apps/web/src/lib/supabase/database.types.ts
git commit -m "feat(db): add prd run kind + dispatch_prd_draft (us-3.21)"
```

---

### Task 2: `db.py` — pool capability filter, claim status, completion artifact write

**Files:**
- Modify: `apps/api/app/db.py:246-289` (`list_worker_pool`), `:323-353` (`worker_allowed_for_run`), `:379-425` (`claim_run`), `:36-166` (`complete_run`)
- Test: `apps/api/tests/test_worker_pool_sql.py` (extend)

**Interfaces:**
- Consumes: `runs.kind` now includes `'prd'` (Task 1).
- Produces: `complete_run(..., prd: str | None = None)` — Task 3's `perform_submit` calls this with `prd=body.prd` for `kind == 'prd'` runs, mirroring how it already calls it with `plan=body.plan, test_plan=body.test_plan` for plan runs.

- [ ] **Step 1: Extend the capability filter in both `list_worker_pool` and `worker_allowed_for_run`**

In `apps/api/app/db.py`, both queries currently gate on:
```sql
or exists (
  select 1 from public.worker_capabilities wc
  where wc.worker_id = %(worker)s
    and wc.project_id = i.project_id
    and ((r.kind = 'plan' and wc.can_plan)
         or (r.kind = 'code' and wc.can_code))
)
```
Change both occurrences (in `list_worker_pool` at line ~278 and `worker_allowed_for_run` at line ~347) to:
```sql
or exists (
  select 1 from public.worker_capabilities wc
  where wc.worker_id = %(worker)s
    and wc.project_id = i.project_id
    and ((r.kind = 'plan' and wc.can_plan)
         or (r.kind = 'prd' and wc.can_plan)
         or (r.kind = 'code' and wc.can_code))
)
```
(PRD drafting is non-code thinking work, so it rides the existing `can_plan` capability rather than adding a third capability column.)

- [ ] **Step 2: `claim_run` — don't force `issues.status` for a `prd` claim**

Replace the block in `claim_run` (`db.py:401-406`):
```python
        issue_status = "planning" if run["kind"] == "plan" else "running"
        conn.execute(
            "update public.issues set status = %s where id = %s",
            (issue_status, run["issue_id"]),
        )
```
with:
```python
        if run["kind"] == "plan":
            issue_status = "planning"
        elif run["kind"] == "code":
            issue_status = "running"
        else:
            issue_status = None  # prd: no issue-status change while claimed
        if issue_status:
            conn.execute(
                "update public.issues set status = %s where id = %s",
                (issue_status, run["issue_id"]),
            )
```

- [ ] **Step 3: `complete_run` — accept `prd` content and write the artifact on success**

Add the parameter to the signature (`db.py:36-53`), right after `test_plan`:
```python
def complete_run(
    settings: Settings,
    run_id: str,
    outcome: str,
    stdout: str | None,
    diff: str | None,
    branch_ref: str | None,
    pr_url: str | None,
    error: str | None,
    test_cases: list[dict[str, Any]] | None = None,
    plan: str | None = None,
    test_plan: str | None = None,
    prd: str | None = None,
    tokens_in: int | None = None,
    tokens_out: int | None = None,
    cost_usd: float | None = None,
    worker_name: str | None = None,
    trigger: str | None = None,
) -> bool:
```

Replace the outcome/event resolution block (`db.py:89-98`):
```python
        kind = run.get("kind") or "code"
        if outcome != "succeeded":
            issue_status = "failed"
            event = "run-failed"
        elif kind == "plan":
            issue_status = "plan-review"
            event = "plan-ready"
        else:
            issue_status = "in-review"
            event = "run-succeeded"
```
with:
```python
        kind = run.get("kind") or "code"
        if outcome != "succeeded":
            issue_status = "failed"
            event = "run-failed"
        elif kind == "plan":
            issue_status = "plan-review"
            event = "plan-ready"
        elif kind == "prd":
            issue_status = "prd-review"
            event = "prd-drafted"
        else:
            issue_status = "in-review"
            event = "run-succeeded"
```

Add a `prd` artifact-write branch right after the existing `elif outcome == "succeeded" and kind == "code":` block (`db.py:129-149`) — insert a new `elif` between the `plan` branch (ends `db.py:128`) and the `code` branch:
```python
        elif outcome == "succeeded" and kind == "prd":
            # Supersede prior draft/approved PRD artifacts; store a new version
            # (mirrors the old synchronous draft_prd endpoint, US-2.3).
            conn.execute(
                """
                update public.artifacts
                set status = 'superseded'
                where issue_id = %s and kind = 'prd' and status in ('draft', 'approved')
                """,
                (run["issue_id"],),
            )
            version = _next_artifact_version(conn, str(run["issue_id"]), "prd")
            content = prd or (stdout or "# PRD\n\n(empty agent draft)")
            conn.execute(
                """
                insert into public.artifacts
                  (org_id, issue_id, kind, content, version, status, created_by)
                values (%s, %s, 'prd', %s, %s, 'draft', 'agent')
                """,
                (run["org_id"], run["issue_id"], content.strip(), version),
            )
```

- [ ] **Step 4: Write the SQL-level test**

Add to `apps/api/tests/test_worker_pool_sql.py` (reuse the module's existing `db`/`ctx`/`workers` fixtures — a live-DB test, skipped if `DATABASE_URL` is unreachable, matching every other test in that file):
```python
def test_prd_claim_and_submit_reaches_prd_review(db, ctx, workers):
    from app import db as appdb

    issue_id = db.execute(
        """
        insert into public.issues (org_id, project_id, type, title, status)
        values (%s, %s, 'feature', 'PRD pool test feature', 'draft')
        returning id
        """,
        (ctx["org_id"], ctx["project_id"]),
    ).fetchone()["id"]
    db.commit()

    run_id = db.execute(
        "select public.dispatch_prd_draft(%s)", (str(issue_id),)
    ).fetchone()["dispatch_prd_draft"]
    db.commit()

    worker = workers[0]
    claimed = appdb.claim_run(ctx["settings"], str(run_id), worker)
    assert claimed is not None
    assert claimed["kind"] == "prd"

    # claiming a prd run must NOT flip the issue into 'planning'/'running'
    issue_status = db.execute(
        "select status from public.issues where id = %s", (issue_id,)
    ).fetchone()["status"]
    assert issue_status == "draft"

    ok = appdb.complete_run(
        ctx["settings"], str(run_id), "succeeded", "stdout", None, None, None, None,
        prd="## Problem\n\nx\n\n## Goals\n\nx\n\n## Out of scope\n\nx\n\n## Acceptance criteria\n\nx\n",
        worker_name=worker["name"],
    )
    assert ok is True

    issue_status = db.execute(
        "select status from public.issues where id = %s", (issue_id,)
    ).fetchone()["status"]
    assert issue_status == "prd-review"

    artifact = db.execute(
        "select kind, status, version, created_by from public.artifacts "
        "where issue_id = %s and kind = 'prd' order by version desc limit 1",
        (issue_id,),
    ).fetchone()
    assert artifact["status"] == "draft"
    assert artifact["created_by"] == "agent"
    assert artifact["version"] == 1
```
Adjust fixture names (`db`, `ctx`, `workers`) to whatever the file's existing fixtures are actually called — read `test_worker_pool_sql.py`'s top before writing this so the insert/commit style matches exactly (module-scoped `db`/`settings`, function-scoped `ctx`/`workers` per the earlier research).

- [ ] **Step 5: Run it**

```bash
cd apps/api && python -m pytest tests/test_worker_pool_sql.py -k prd -v
```
Expected: PASS (or SKIPPED if `DATABASE_URL` isn't set in the test environment — that's the existing convention for this file, not a failure).

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/db.py apps/api/tests/test_worker_pool_sql.py
git commit -m "feat(api): worker pool + completion handling for prd runs (us-3.21)"
```

---

### Task 3: `worker.py` — `Submit.prd`, `perform_submit` branch, context bundle skips git for `prd`

**Files:**
- Modify: `apps/api/app/routers/worker.py:98-134` (`context`), `:158-177` (`Submit`), `:183-373` (`perform_submit`)
- Test: `apps/api/tests/test_worker_pool.py` (extend)

**Interfaces:**
- Consumes: `db.complete_run(..., prd=...)` from Task 2.
- Produces: `Submit.prd: str | None` — Task 4's MCP `submit_prd` tool constructs this same `Submit` model.

- [ ] **Step 1: Add the field to `Submit`**

In the `Submit` pydantic model (`worker.py:158-177`), add under the `# plan runs` fields:
```python
class Submit(BaseModel):
    # plan runs
    plan: str | None = None
    test_plan: str | None = None
    # prd runs
    prd: str | None = None
    # code runs
    branch_ref: str | None = None
    ...
```

- [ ] **Step 2: Branch `perform_submit` for `kind == "prd"`**

Insert a new branch in `perform_submit` right before the existing `if run["kind"] == "plan":` block (`worker.py:246`):
```python
    if run["kind"] == "prd":
        if not body.prd:
            raise HTTPException(status_code=422, detail="prd markdown is required")
        ok = db.complete_run(
            settings,
            run_id,
            "succeeded",
            body.stdout,
            None,
            None,
            None,
            None,
            prd=body.prd,
            worker_name=worker["name"],
            trigger=trigger,
            **usage,
        )
        if not ok:
            raise HTTPException(
                status_code=409, detail="run is not claimed — claim it and retry"
            )
        return {"ok": True, "issue_status": "prd-review"}

    if run["kind"] == "plan":
        ...
```

- [ ] **Step 3: Skip git fields in `context` for `prd` runs**

Replace the `context` endpoint body (`worker.py:98-134`) with:
```python
@router.get("/runs/{run_id}/context")
async def context(
    run_id: str,
    request: Request,
    worker: dict = Depends(verify_worker),
    settings: Settings = Depends(get_settings),
):
    run = db.get_worker_run(settings, run_id, str(worker["org_id"]))
    if not run:
        raise HTTPException(status_code=404, detail="run not found")
    if str(run.get("worker_id") or "") != str(worker["id"]):
        raise HTTPException(
            status_code=409, detail="you do not hold this run — claim it first"
        )
    db.extend_claim(settings, run_id, str(worker["id"]))

    ic = run.get("input_context") or {}
    if run["kind"] == "prd":
        return {
            "run_id": str(run["id"]),
            "kind": run["kind"],
            "issue_id": str(run["issue_id"]),
            "context": ic,
            "instructions": (
                "This is a PRD-drafting run — no repo, no branch, no git remote. "
                "Write the four PRD sections as markdown (## Problem, ## Goals, "
                "## Out of scope, ## Acceptance criteria) and submit with `prd`."
            ),
        }

    base = str(request.base_url).rstrip("/")
    branch = f"factory/issue-{run['issue_id']}"
    return {
        "run_id": str(run["id"]),
        "kind": run["kind"],
        "issue_id": str(run["issue_id"]),
        "context": ic,
        "branch_name": branch,
        "git_remote_url": (
            f"{base}/git/{run['org_shortname']}/{run['project_slug']}.git"
        ),
        "repo_full_name": ic.get("repo_full_name"),
        "default_branch": ic.get("default_branch"),
        "instructions": (
            f"Clone the factory git remote (HTTP Basic auth — password is this "
            f"same worker token), work on branch '{branch}', push it, then "
            f"submit with the branch ref. No GitHub credentials and no PR "
            f"needed — the factory opens the PR itself on submit."
        ),
    }
```

- [ ] **Step 4: Write endpoint-level tests**

Add to `apps/api/tests/test_worker_pool.py`, following the existing `worker_auth`/`client`/`monkeypatch` pattern in that file:
```python
def test_prd_context_has_no_git_fields(client, worker_auth, monkeypatch):
    monkeypatch.setattr(
        "app.routers.worker.db.get_worker_run",
        lambda s, r, org: {
            "id": RUN_ID, "org_id": ORG_ID, "issue_id": ISSUE_ID,
            "worker_id": WORKER["id"], "status": "running", "kind": "prd",
            "input_context": {"title": "A feature"},
        },
    )
    monkeypatch.setattr("app.routers.worker.db.extend_claim", lambda s, r, w: True)
    resp = client.get(f"/api/v1/worker/runs/{RUN_ID}/context", headers=HDR)
    assert resp.status_code == 200
    body = resp.json()
    assert "git_remote_url" not in body
    assert "branch_name" not in body
    assert body["context"]["title"] == "A feature"


def test_prd_submit_requires_prd_field(client, worker_auth, monkeypatch):
    monkeypatch.setattr(
        "app.routers.worker.db.get_worker_run",
        lambda s, r, org: {
            "id": RUN_ID, "org_id": ORG_ID, "issue_id": ISSUE_ID,
            "worker_id": WORKER["id"], "status": "running", "kind": "prd",
            "input_context": {},
        },
    )
    resp = client.post(f"/api/v1/worker/runs/{RUN_ID}/submit", headers=HDR, json={})
    assert resp.status_code == 422


def test_prd_submit_reaches_prd_review(client, worker_auth, monkeypatch):
    monkeypatch.setattr(
        "app.routers.worker.db.get_worker_run",
        lambda s, r, org: {
            "id": RUN_ID, "org_id": ORG_ID, "issue_id": ISSUE_ID,
            "worker_id": WORKER["id"], "status": "running", "kind": "prd",
            "input_context": {},
        },
    )
    captured = {}

    def fake_complete(settings, run_id, outcome, stdout, diff, branch_ref, pr_url, error, **kw):
        captured.update(outcome=outcome, prd=kw.get("prd"))
        return True

    monkeypatch.setattr("app.routers.worker.db.complete_run", fake_complete)
    resp = client.post(
        f"/api/v1/worker/runs/{RUN_ID}/submit",
        headers=HDR,
        json={"prd": "## Problem\n\nx\n"},
    )
    assert resp.status_code == 200
    assert resp.json()["issue_status"] == "prd-review"
    assert captured["prd"] == "## Problem\n\nx\n"
```

- [ ] **Step 5: Run it**

```bash
cd apps/api && python -m pytest tests/test_worker_pool.py -k prd -v
```
Expected: 3 PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/routers/worker.py apps/api/tests/test_worker_pool.py
git commit -m "feat(api): worker /runs context+submit support prd runs (us-3.21)"
```

---

### Task 4: `factory_mcp.py` — `submit_prd` tool + `get_work_context` skips git for `prd`

**Files:**
- Modify: `apps/api/app/factory_mcp.py:136-183` (`get_work_context`), add `submit_prd` after `submit_code_work` (`:244`)
- Test: `apps/api/tests/test_factory_mcp.py` (extend)

**Interfaces:**
- Consumes: `Submit`/`perform_submit` from `apps/api/app/routers/worker.py` (Task 3), same import pattern as `submit_plan`/`submit_code_work`.

- [ ] **Step 1: Skip git fields in `get_work_context` for `prd` runs**

In `get_work_context` (`factory_mcp.py:136-182`), right after `ic = run.get("input_context") or {}` (line 149), branch before building `branch`/`remote`:
```python
    ic = run.get("input_context") or {}
    if run["kind"] == "prd":
        md = (
            f"# {run.get('issue_title') or ic.get('title', 'Work item')}\n\n"
            f"- Kind: **prd** (no repo, no branch — submit markdown with submit_prd)\n\n"
            f"## Raw idea\n\n{ic.get('story') or ic.get('body') or '(no body)'}\n"
        )
        for key, title in (
            ("previous_prd", "Prior draft"),
            ("feedback", "Send-back feedback (this is a retry)"),
            ("guidelines", "Project guidelines"),
            ("learnings", "Project learnings"),
        ):
            if ic.get(key):
                md += f"\n\n## {title}\n\n{ic[key]}"
        return {"markdown": md, "context": ic, "kind": "prd"}

    request = ctx.request_context.request if ctx.request_context else None
    base = str(request.base_url).rstrip("/") if request is not None else ""
    branch = f"factory/issue-{run['issue_id']}"
    ...  # existing code unchanged below this point
```

- [ ] **Step 2: Add the `submit_prd` tool**

Add right after `submit_code_work` (`factory_mcp.py`, after line 243):
```python
@mcp.tool()
async def submit_prd(
    run_id: str,
    prd: str,
    stdout: str = "",
) -> dict[str, Any]:
    """Hand back a prd run: the four PRD sections as markdown (## Problem,
    ## Goals, ## Out of scope, ## Acceptance criteria)."""
    from .routers.worker import Submit, perform_submit

    settings = get_settings()
    worker = _worker()
    try:
        result = await perform_submit(
            settings,
            worker,
            run_id,
            Submit(prd=prd, stdout=stdout or None),
        )
    except HTTPException as e:
        return _err(str(e.detail), "check claim_work / the run id and retry")
    return {
        "markdown": "PRD submitted — it now sits in the prd-review gate.",
        **result,
    }
```

- [ ] **Step 3: Update the MCP tool-count test**

`test_factory_mcp.py` has `test_all_six_tools_listed` — rename/update it to expect seven tools including `submit_prd`. Find the assertion (likely a list of tool names or `len(tools) == 6`) and add `"submit_prd"`, changing the count to 7.

- [ ] **Step 4: Add a `submit_prd` MCP tool test**

Following the file's existing `mcp_client` fixture pattern (module-scoped), add:
```python
async def test_submit_prd_delegates(mcp_client, monkeypatch):
    captured = {}

    async def fake_perform_submit(settings, worker, run_id, body, trigger="submit"):
        captured.update(run_id=run_id, prd=body.prd)
        return {"ok": True, "issue_status": "prd-review"}

    monkeypatch.setattr(
        "app.routers.worker.perform_submit", fake_perform_submit
    )
    result = await mcp_client.call_tool(
        "submit_prd", {"run_id": "r1", "prd": "## Problem\n\nx\n"}
    )
    assert captured["run_id"] == "r1"
    assert captured["prd"] == "## Problem\n\nx\n"
```
Match this to whatever calling convention (`mcp_client.call_tool` vs. direct import) the existing `submit_plan`/`submit_code_work` tests in that file already use — read those tests first and mirror the exact pattern rather than inventing a new one.

- [ ] **Step 5: Run it**

```bash
cd apps/api && python -m pytest tests/test_factory_mcp.py -v
```
Expected: all PASS, including the updated seven-tools assertion.

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/factory_mcp.py apps/api/tests/test_factory_mcp.py
git commit -m "feat(api): submit_prd MCP tool + prd-aware get_work_context (us-3.21)"
```

---

### Task 5: `workflow.py` — `draft_prd` dispatches instead of calling the LLM inline

**Files:**
- Modify: `apps/api/app/routers/workflow.py:221-360`
- Test: `apps/api/tests/test_workflow.py` (replace `test_draft_prd_simulates_without_llm`)

**Interfaces:**
- Consumes: `dispatch_prd_draft` RPC function (Task 1), called the same way `issues.py`'s `dispatch` endpoint calls `dispatch_issue` (`rpc(settings, user.token, "dispatch_prd_draft", {"p_issue": str(issue_id)})`).
- Produces: `POST /issues/{issue_id}/prd/draft` now returns `{"run_id": <uuid str>, "status": "queued"}` instead of `{"artifact": ..., "status": "prd-review", "wireframes": [...], "wireframe_error": ...}`. Task 6 (frontend) consumes this new shape.

- [ ] **Step 1: Replace the `draft_prd` endpoint body**

Replace `workflow.py:221-360` entirely with:
```python
@router.post("/issues/{issue_id}/prd/draft")
async def draft_prd(
    issue_id: UUID,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """US-3.21: dispatches a `kind='prd'` run into the worker pool instead
    of calling the LLM inline — the runner claims it by default, or a
    connected human/agent worker can claim it first."""
    try:
        run_id = await rpc(
            settings, user.token, "dispatch_prd_draft", {"p_issue": str(issue_id)}
        )
    except RpcError as e:
        if "not found" in e.message:
            raise HTTPException(status_code=404, detail="Issue not found")
        if "only for feature" in e.message or "cannot draft PRD" in e.message:
            raise HTTPException(status_code=409, detail=e.message)
        raise HTTPException(status_code=400, detail=e.message)

    return {"run_id": run_id, "status": "queued"}
```

- [ ] **Step 2: Remove the now-unreachable `_llm_or_simulate`/wireframe wiring from this endpoint only**

Leave `_llm_or_simulate`, `_wants_wireframes`, `_generate_prd_wireframes`, `_parse_wireframes`, `_WIREFRAME_RE`, etc. untouched in the file — they are dead code after this change (nothing else calls `_generate_prd_wireframes`), which is a known, called-out tradeoff (see Global Constraints), not something to silently clean up in this task.

- [ ] **Step 3: Replace the test**

In `apps/api/tests/test_workflow.py`, replace `test_draft_prd_simulates_without_llm` (which asserted a synchronous artifact came back) with:
```python
def test_draft_prd_dispatches_a_run(client, auth_headers, monkeypatch):
    run_id = str(uuid.uuid4())

    async def fake_rpc(settings, token, fn, args):
        assert fn == "dispatch_prd_draft"
        assert args == {"p_issue": ISSUE_ID}
        return run_id

    monkeypatch.setattr("app.routers.workflow.rpc", fake_rpc)
    resp = client.post(f"/api/v1/issues/{ISSUE_ID}/prd/draft", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == {"run_id": run_id, "status": "queued"}


def test_draft_prd_wrong_status_is_409(client, auth_headers, monkeypatch):
    from app.supabase import RpcError

    async def fake_rpc(settings, token, fn, args):
        raise RpcError('cannot draft PRD from status "planning"')

    monkeypatch.setattr("app.routers.workflow.rpc", fake_rpc)
    resp = client.post(f"/api/v1/issues/{ISSUE_ID}/prd/draft", headers=auth_headers)
    assert resp.status_code == 409
```
Use whatever `ISSUE_ID`/`auth_headers`/`client` fixtures the file already defines (read the top of `test_workflow.py` first — the existing `test_draft_prd_simulates_without_llm` shows the exact fixture names to reuse) rather than inventing new ones. `RpcError`'s constructor signature must match `apps/api/app/supabase.py`'s actual definition — check it (likely `RpcError(message: str)`) before writing this.

- [ ] **Step 4: Run it**

```bash
cd apps/api && python -m pytest tests/test_workflow.py -k draft_prd -v
```
Expected: both new tests PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/routers/workflow.py apps/api/tests/test_workflow.py
git commit -m "feat(api): draft_prd dispatches a worker-pool run (us-3.21)"
```

---

### Task 6: `provider_sim.py` — simulated `prd` fulfillment

**Files:**
- Modify: `apps/runner/provider_sim.py`

**Interfaces:**
- Produces: `ProviderResult.prd: str | None` — Task 8 (runner.py) forwards this in the submit payload.

- [ ] **Step 1: Add the field to `ProviderResult`**

```python
@dataclass
class ProviderResult:
    outcome: str  # succeeded | failed
    stdout: str | None = None
    diff: str | None = None
    branch_ref: str | None = None
    pr_url: str | None = None
    error: str | None = None
    test_cases: list | None = None
    plan: str | None = None
    test_plan: str | None = None
    prd: str | None = None
```

- [ ] **Step 2: Add `_prd_result` and branch `execute()`**

Add before `execute`:
```python
def _prd_result(ctx: dict) -> ProviderResult:
    title = ctx.get("title", "feature")
    body = (ctx.get("story") or ctx.get("body") or "")[:200]
    feedback = ctx.get("feedback")
    content = (
        f"## Problem\n\nSimulated PRD for “{title}”. {body}\n\n"
        f"## Goals\n\n- Deliver “{title}” as described in the raw idea\n\n"
        f"## Out of scope\n\n- Anything not explicitly listed above\n\n"
        f"## Acceptance criteria\n\n- “{title}” behaves as described\n"
    )
    if feedback:
        content += f"\n## Addressing feedback\n\n{feedback}\n"
    return ProviderResult(
        outcome="succeeded",
        stdout=f"[sim] prd run started: {title}\n[sim] wrote PRD draft",
        prd=content,
    )
```
In `execute()`, add the branch right after the `run_kind = ctx.get("run_kind") or "code"` line and before the `stuck`/`fail` scenario checks are resolved into their branches — specifically, insert after the `if scenario == "fail": ...` block and before `if run_kind == "plan":`:
```python
    if run_kind == "prd":
        time.sleep(1)
        return _prd_result(ctx)

    if run_kind == "plan":
        ...
```
(Keep the `stuck`/`fail` scenario checks above this — they must still apply to `prd` runs too, since `[sim:stuck]`/`[sim:fail]` in the title/body should still simulate a failure regardless of kind. Only the success-path branch changes.)

- [ ] **Step 3: Smoke-test manually**

```bash
cd apps/runner && python -c "
import provider_sim
r = provider_sim.execute({'run_kind': 'prd', 'title': 'CSV export', 'story': 'Let users export data'})
assert r.outcome == 'succeeded'
assert '## Problem' in r.prd
print('ok')
"
```
Expected: `ok`.

- [ ] **Step 4: Commit**

```bash
git add apps/runner/provider_sim.py
git commit -m "feat(runner): simulated prd run fulfillment (us-3.21)"
```

---

### Task 7: `provider_claude.py` — real `prd` fulfillment (no checkout)

**Files:**
- Modify: `apps/runner/provider_claude.py`

**Interfaces:**
- Consumes: `ProviderResult` (now with `.prd`) from Task 6's `provider_sim.py` (this file already does `from provider_sim import ProviderResult`).

- [ ] **Step 1: Add a scratch-dir PRD execution path**

Add before `execute`:
```python
def _build_prd_prompt(ctx: dict) -> str:
    sections = [
        "Write a product requirements document (PRD) for this feature. "
        "Respond with ONLY markdown containing exactly these headings, in "
        "this order: ## Problem, ## Goals, ## Out of scope, "
        "## Acceptance criteria. No other text before or after.",
        f"# {ctx.get('title', 'Feature')}",
    ]
    for key, heading in (
        ("story", "Raw idea"),
        ("previous_prd", "Prior draft"),
        ("feedback", "Send-back feedback — address it"),
        ("guidelines", "Project guidelines"),
        ("learnings", "Project learnings"),
    ):
        value = ctx.get(key)
        if value:
            sections.append(f"## Context: {heading}\n{value}")
    return "\n\n".join(sections)


def _execute_prd(ctx: dict, timeout_seconds: int) -> ProviderResult:
    """No git checkout — the CLI is invoked in a scratch dir purely as an
    LLM call, and its stdout *is* the PRD markdown."""
    scratch = _workspace() / "prd-scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    ok, stdout = _run_cli(_build_prd_prompt(ctx), scratch, timeout_seconds)
    if not ok:
        return ProviderResult(
            outcome="failed",
            stdout=stdout[-20000:],
            error="Claude Code CLI failed or timed out",
        )
    content = stdout.strip()
    if not content:
        return ProviderResult(
            outcome="failed", stdout=stdout[-20000:], error="CLI produced no PRD content"
        )
    return ProviderResult(outcome="succeeded", stdout=stdout[-20000:], prd=content)
```

- [ ] **Step 2: Branch `execute()` before the git-required checks**

In `execute()`, the current first lines are:
```python
def execute(ctx: dict, timeout_seconds: int = 1200) -> ProviderResult:
    run_kind = ctx.get("run_kind") or "code"
    token = os.environ.get("FACTORY_WORKER_TOKEN", "")
    remote = ctx.get("git_remote_url") or ""
    branch = ctx.get("branch_name") or ctx.get("previous_branch") or ""
    default_branch = ctx.get("default_branch") or "main"

    if not remote or not branch:
        return ProviderResult(
            outcome="failed",
            error="context is missing git_remote_url or branch_name",
        )
```
Change to:
```python
def execute(ctx: dict, timeout_seconds: int = 1200) -> ProviderResult:
    run_kind = ctx.get("run_kind") or "code"
    if run_kind == "prd":
        return _execute_prd(ctx, timeout_seconds)

    token = os.environ.get("FACTORY_WORKER_TOKEN", "")
    remote = ctx.get("git_remote_url") or ""
    branch = ctx.get("branch_name") or ctx.get("previous_branch") or ""
    default_branch = ctx.get("default_branch") or "main"

    if not remote or not branch:
        return ProviderResult(
            outcome="failed",
            error="context is missing git_remote_url or branch_name",
        )
```
(everything below is unchanged — `try/except` block for `plan`/`code` fulfillment stays exactly as-is.)

- [ ] **Step 3: Commit**

```bash
git add apps/runner/provider_claude.py
git commit -m "feat(runner): real-CLI prd fulfillment without repo checkout (us-3.21)"
```
(No automated test for this file — it shells out to the real `claude` CLI, same as the rest of `provider_claude.py`, which has no pytest coverage today either. Verified manually in Task 9's end-to-end check.)

---

### Task 8: `runner.py` — forward `result.prd` in the submit payload

**Files:**
- Modify: `apps/runner/runner.py:145-154`

**Interfaces:**
- Consumes: `ProviderResult.prd` (Task 6/7).

- [ ] **Step 1: Add `prd` to the success payload**

```python
        if result.outcome == "succeeded":
            payload = {
                "stdout": result.stdout,
                "plan": result.plan,
                "test_plan": result.test_plan,
                "prd": result.prd,
                "branch_ref": result.branch_ref,
                "pr_url": result.pr_url,
                "diff": result.diff,
                "test_cases": result.test_cases,
            }
```
(The existing `submit(env, run_id, {k: v for k, v in payload.items() if v is not None})` call already strips `None` values, so this is safe for `plan`/`code` runs where `result.prd` is `None`.)

- [ ] **Step 2: Commit**

```bash
git add apps/runner/runner.py
git commit -m "feat(runner): forward prd content in submit payload (us-3.21)"
```

---

### Task 9: `prd-panel.tsx` — dispatch instead of await, live queued/in-progress state

**Files:**
- Modify: `apps/web/src/app/(app)/issues/[id]/prd-panel.tsx`

**Interfaces:**
- Consumes: `POST /api/v1/issues/{issueId}/prd/draft` now returns `{"run_id": string, "status": "queued"}` (Task 5).
- Consumes: a new `hasActivePrdRun: boolean` prop, passed from `apps/web/src/app/(app)/issues/[id]/page.tsx`, which already fetches every `runs` row for the issue (`kind`, `status` are already selected — `page.tsx`'s existing `runs` query at the `.select(...)` call needs no column changes, only a new derived boolean passed down).

- [ ] **Step 1: Wire `hasActivePrdRun` from `page.tsx` into `PrdPanel`**

In `apps/web/src/app/(app)/issues/[id]/page.tsx`, after the existing `runs` fetch, add:
```tsx
  const hasActivePrdRun = (runs ?? []).some(
    (r) => r.kind === "prd" && ["queued", "running"].includes(r.status)
  );
```
And pass it to `<PrdPanel ... />`:
```tsx
      {type === "feature" && (
        <PrdPanel
          issueId={issue.id}
          orgId={issue.org_id}
          projectId={issue.project_id}
          status={issue.status}
          artifacts={prdArtifacts}
          documents={prdDocs}
          actorNames={actorNames}
          hasActivePrdRun={hasActivePrdRun}
        />
      )}
```

- [ ] **Step 2: Rewrite `draftPrd` and add the queued/in-progress render + Realtime subscription**

In `prd-panel.tsx`:
- Add imports: `import { useEffect } from "react";` (extend the existing `useState` import line) and `import { createClient } from "@/lib/supabase/client";`.
- Add the `hasActivePrdRun` prop to the function signature and its type block:
```tsx
export function PrdPanel({
  issueId,
  orgId,
  projectId,
  status,
  artifacts,
  documents,
  actorNames,
  hasActivePrdRun,
}: {
  issueId: string;
  orgId: string;
  projectId: string;
  status: string;
  artifacts: PrdArtifact[];
  documents: DocumentRow[];
  actorNames?: Record<string, string>;
  hasActivePrdRun: boolean;
}) {
```
- Replace `canDraft`:
```tsx
  const canDraft =
    !draft &&
    !hasActivePrdRun &&
    (status === "draft" || status === "prd-review" || status === "ready");
```
- Replace `draftPrd`:
```tsx
  async function draftPrd() {
    setError(null);
    setWireframeError(null);
    setBusy("draft");
    try {
      await apiFetch(`/api/v1/issues/${issueId}/prd/draft`, { method: "POST" });
      router.refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }
```
- Add a Realtime subscription (mirrors `apps/web/src/components/stage-tracker.tsx`'s existing pattern exactly — same channel-setup shape, filtered to this issue's runs) right after the `sections`/`comment` state declarations:
```tsx
  useEffect(() => {
    if (!hasActivePrdRun) return;
    const supabase = createClient();
    let channel: ReturnType<typeof supabase.channel> | null = null;
    let cancelled = false;

    async function subscribe() {
      const {
        data: { session },
      } = await supabase.auth.getSession();
      if (!session || cancelled) return;
      await supabase.realtime.setAuth(session.access_token);
      channel = supabase
        .channel(`prd-panel-${issueId}`, { config: { private: false } })
        .on(
          "postgres_changes",
          {
            event: "*",
            schema: "public",
            table: "runs",
            filter: `issue_id=eq.${issueId}`,
          },
          () => router.refresh()
        )
        .subscribe();
    }

    subscribe();
    return () => {
      cancelled = true;
      if (channel) supabase.removeChannel(channel);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [issueId, hasActivePrdRun]);
```
- In the header's button render, show a queued/in-progress state instead of nothing once dispatched:
```tsx
        {canDraft && (
          <Button onClick={draftPrd} disabled={busy !== null}>
            {busy === "draft" ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <Sparkles className="size-4" />
            )}
            Draft PRD
          </Button>
        )}
        {hasActivePrdRun && !draft && (
          <span className="flex items-center gap-1.5 text-sm text-muted-foreground">
            <Loader2 className="size-3.5 animate-spin" />
            Drafting — waiting for a worker to pick this up…
          </span>
        )}
```

- [ ] **Step 3: Manual verification (this is a UI change — verify in the running app per project convention)**

```bash
npm run dev
```
Then, with the runner running against the same API (`RUNNER_PROVIDER=sim python apps/runner/runner.py`, or the real one), open a feature issue's detail page, click **Draft PRD**, and confirm: the button disappears and a "Drafting — waiting for a worker…" indicator appears immediately; within a few seconds (once the runner claims + the sim/real provider completes) the page auto-refreshes via Realtime and shows the new draft artifact card with Approve/Send back, exactly as before this change.

- [ ] **Step 4: Commit**

```bash
git add apps/web/src/app/(app)/issues/[id]/prd-panel.tsx apps/web/src/app/(app)/issues/[id]/page.tsx
git commit -m "feat(web): PRD panel dispatches + shows live queued state (us-3.21)"
```

---

## Self-Review Notes (already applied above, kept here for the executor's context)

- **Spec coverage:** every bullet in `stories/us-3.21-dispatch-prd-drafting.md`'s acceptance criteria maps to a task: migration/constraint (Task 1), pool listing + claim + context + submit + release for `prd` (Tasks 2–3; release needed no code change — it's already kind-agnostic in `db.release_claim`), runner default-claim fulfillment (Tasks 6–8), MCP `submit_prd` (Task 4), no-git context bundle (Task 3 Step 3 + Task 4 Step 1), issue_events (`prd-dispatched` in Task 1, `prd-drafted` reused in Task 2 Step 3), pytest coverage (Tasks 2–5 each add tests).
- **Known, called-out gap:** wireframe auto-generation on PRD draft stops working after Task 5 (see Global Constraints) — this matches the story's explicit out-of-scope line but is a real behavior change worth flagging to the user once this plan ships, since a real user might be relying on it today.
- **Type/name consistency check:** `Submit.prd` (Task 3) ↔ `ProviderResult.prd` (Task 6/7) ↔ `runner.py` payload key `"prd"` (Task 8) ↔ `db.complete_run(..., prd=...)` (Task 2) ↔ MCP tool param `prd` (Task 4) — all the same name throughout, deliberately matching `artifacts.kind = 'prd'`.
