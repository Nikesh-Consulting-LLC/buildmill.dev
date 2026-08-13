---
id: "BUG-1.1"
issue_id: "6c431050-c7a7-46f7-9a71-dbfa1ff9f7b2"
type: "story"
title: "HTTP Error when Trying to delete an item from Deployment"
parent: null
epic: 1
order: 1
has_plan: true
has_test_plan: true
merge_commit: "cf72aa9cc47baf6420861cbc2e64b4a30eb6e888"
generated_at: "2026-07-29T01:02:57+00:00"
---

# BUG-1.1 — HTTP Error when Trying to delete an item from Deployment

> Approved story and plan, written by Build Mill. The app owns this file; edits here are overwritten on the next approval.

## Story

## Repro

HTTPStatusError: Redirect response '300 Multiple Choices' for url 'https://wdudmfhhqxrqzoyhuzwx.supabase.co/rest/v1/deployments?select=%2A%2Cservers%28%2A%29%2Cprojects%28id%2Cname%2Crepo_full_name%2Cuat_branch%2Cproduction_branch%29&id=eq.29e6a69d-cdae-418f-8ebd-ec717894b86c&limit=1'For more infor… [truncated at 300 characters]

--------------------------------------

Redirect response '300 Multiple Choices' for url 'https://wdudmfhhqxrqzoyhuzwx.supabase.co/rest/v1/deployments?select=%2A%2Cservers%28%2A%29%2Cprojects%28id%2Cname%2Crepo_full_name%2Cuat_branch%2Cproduction_branch%29&id=eq.29e6a69d-cdae-418f-8ebd-ec717894b86c&limit=1'
For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/300

```
Traceback (most recent call last):
  File "/opt/factory.nexdb.cloud/apps/api/.venv/lib/python3.13/site-packages/starlette/middleware/errors.py", line 164, in __call__
    await self.app(scope, receive, _send)
  File "/opt/factory.nexdb.cloud/apps/api/.venv/lib/python3.13/site-packages/starlette/middleware/base.py", line 193, in __call__
    response = await self.dispatch_func(request, call_next)
               ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/opt/factory.nexdb.cloud/apps/api/app/main.py", line 159, in report_endpoint_cors
    return await call_next(request)
           ^^^^^^^^^^^^^^^^^^^^^^^^
  File "/opt/factory.nexdb.cloud/apps/api/.venv/lib/python3.13/site-packages/starlette/middleware/base.py", line 168, in call_next
    raise app_exc from app_exc.__cause__ or app_exc.__context__
  File "/opt/factory.nexdb.cloud/apps/api/.venv/lib/python3.13/site-packages/starlette/middleware/base.py", line 144, in coro
    await self.app(scope, receive_or_disconnect, send_no_error)
  File "/opt/factory.nexdb.cloud/apps/api/.venv/lib/python3.13/site-packages/starlette/middleware/cors.py", line 96, in __call__
    await self.simple_response(scope, receive, send, request_headers=headers)
  File "/opt/factory.nexdb.cloud/apps/api/.venv/lib/python3.13/site-packages/starlette/middleware/cors.py", line 154, in simple_response
    await self.app(scope, receive, send)
  File "/opt/factory.nexdb.cloud/apps/api/.venv/lib/python3.13/site-packages/starlette/middleware/exceptions.py", line 63, in __call__
    await wrap_app_handling_exceptions(self.app, conn)(scope, receive, send)
  File "/opt/factory.nexdb.cloud/apps/api/.venv/lib/python3.13/site-packages/starlette/_exception_handler.py", line 53, in wrapped_app
    raise exc
  File "/opt/factory.nexdb.cloud/apps/api/.venv/lib/python3.13/site-packages/starlette/_exception_handler.py", line 42, in wrapped_app
    await app(scope, receive, sender)
  File "/opt/factory.nexdb.cloud/apps/api/.venv/lib/python3.13/site-packages/fastapi/middleware/asyncexitstack.py", line 18, in __call__
    await self.app(scope, receive, send)
  File "/opt/factory.nexdb.cloud/apps/api/.venv/lib/python3.13/site-packages/starlette/routing.py", line 660, in __call__
    await self.middleware_stack(scope, receive, send)
  File "/opt/factory.nexdb.cloud/apps/api/.venv/lib/python3.13/site-packages/fastapi/routing.py", line 2683, in app
    await route.handle(scope, receive, send)
  File "/opt/factory.nexdb.cloud/apps/api/.venv/lib/python3.13/site-packages/fastapi/routing.py", line 1753, in handle
    await self.original_router.handle(scope, receive, send)
  File "/opt/factory.nexdb.cloud/apps/api/.venv/lib/python3.13/site-packages/fastapi/routing.py", line 2738, in handle
    await included_router._handle_selected(scope, receive, send)
  File "/opt/factory.nexdb.cloud/apps/api/.venv/lib/python3.13/site-packages/fastapi/routing.py", line 1773, in _handle_selected
    await original_route.handle(scope, receive, send)
  File "/opt/factory.nexdb.cloud/apps/api/.venv/lib/python3.13/site-packages/fastapi/routing.py", line 1264, in handle
    await app(scope, receive, send)
  File "/opt/factory.nexdb.cloud/apps/api/.venv/lib/python3.13/site-packages/fastapi/routing.py", line 150, in app
    await wrap_app_handling_exceptions(app, request)(scope, receive, send)
  File "/opt/factory.nexdb.cloud/apps/api/.venv/lib/python3.13/site-packages/starlette/_exception_handler.py", line 53, in wrapped_app
    raise exc
  File "/opt/factory.nexdb.cloud/apps/api/.venv/lib/python3.13/site-packages/starlette/_exception_handler.py", line 42, in wrapped_app
    await app(scope, receive, sender)
  File "/opt/factory.nexdb.cloud/apps/api/.venv/lib/python3.13/site-packages/fastapi/routing.py", line 136, in app
    response = await f(request)
               ^^^^^^^^^^^^^^^^
  File "/opt/factory.nexdb.cloud/apps/api/.venv/lib/python3.13/site-packages/fastapi/routing.py", line 690, in app
    raw_response = await run_endpoint_function(
                   ^^^^^^^^^^^^^^^^^^^^^^^^^^^^
    ...<3 lines>...
    )
    ^
  File "/opt/factory.nexdb.cloud/apps/api/.venv/lib/python3.13/site-packages/fastapi/routing.py", line 344, in run_endpoint_function
    return await dependant.call(**values)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/opt/factory.nexdb.cloud/apps/api/app/routers/deployments.py", line 875, in delete_deployment
    dep = await get_deployment_for_user(settings, user.token, deployment_id)
          ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/opt/factory.nexdb.cloud/apps/api/app/routers/deployments.py", line 40, in get_deployment_for_user
    rows = await postgrest_get(
           ^^^^^^^^^^^^^^^^^^^^
    ...<8 lines>...
    )
    ^
  File "/opt/factory.nexdb.cloud/apps/api/app/supabase.py", line 31, in postgrest_get
    resp.raise_for_status()
    ~~~~~~~~~~~~~~~~~~~~~^^
  File "/opt/factory.nexdb.cloud/apps/api/.venv/lib/python3.13/site-packages/httpx/_models.py", line 829, in raise_for_status
    raise HTTPStatusError(message, request=request, response=self)
httpx.HTTPStatusError: Redirect response '300 Multiple Choices' for url 'https://wdudmfhhqxrqzoyhuzwx.supabase.co/rest/v1/deployments?select=%2A%2Cservers%28%2A%29%2Cprojects%28id%2Cname%2Crepo_full_name%2Cuat_branch%2Cproduction_branch%29&id=eq.29e6a69d-cdae-418f-8ebd-ec717894b86c&limit=1'
For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/300
```

---
Promoted from an app report (automated, 1 occurrence(s), first seen 2026-07-28 21:56, last seen 2026-07-28 21:56).

## Expected

The Deployment should get deleted

## Acceptance criteria

_None recorded._

## Approved implementation plan

# BUG-1.1 — HTTP error when deleting a deployment

## Diagnosis (context for the four sections below)

Every deployment endpoint — run, cancel, rollback, promote, duplicate, env
var add/remove, and delete — authorizes through one shared pre-flight read
that fetches the deployment together with its server and its project. That
read asked PostgREST to embed the project without naming which relationship
to follow.

US-16.1's report inbox is a junction: it carries a NOT NULL foreign key to
deployments *and* a NOT NULL foreign key to projects. From the moment that
migration was applied, PostgREST could reach projects from deployments two
ways — the direct column and the inferred many-to-many — so it refused to
choose and answered `300 Multiple Choices` (PGRST201). The API's PostgREST
client raises on any non-2xx, including 3xx, and nothing catches it, so the
browser got an unhandled 500 with no `detail` and the confirmation dialog
could only say "API error 500". That is the "HTTP Error" in the report.

Two things follow, and the plan is shaped by them:

- The failure was never delete-specific. Deleting was simply the operation
  the reporter happened to try.
- A hint for that one read (and for the one page that embeds the same pair)
  is **already in the working tree**, from the fix that named the constraint
  the junction made ambiguous. What is missing is proof that the delete now
  completes end to end, the same sweep everywhere else, and an error surface
  that names this class of failure the next time instead of hiding it behind
  a traceback. The row delete itself is safe: every foreign key pointing at
  a deployment either cascades or nulls, so nothing blocks it.

## What changes

- A manager can delete a deployment from the project's Deployments tab and
  see it disappear, with its stored env values and run artifacts removed —
  the operation the report says should work.
- The other operations gated by the same pre-flight read — run, cancel,
  rollback, promote, duplicate, and env var edits — are covered by tests
  that fail if that read ever stops resolving, instead of all of them
  breaking silently together the next time the schema grows a junction.
- When a deployment operation cannot complete because Supabase refused the
  query, the caller gets a specific, readable reason carrying the PostgREST
  error code, and the confirmation dialog shows it — instead of an
  unhandled 500 that reaches the user as "API error 500" and reaches the
  operator only as a crash report.
- Every cross-table embed the app depends on that the current schema can no
  longer resolve on its own names its relationship explicitly, so no other
  page or endpoint is one click away from the identical 300.
- A future table that makes an embed the app already relies on ambiguous is
  caught by the test suite, not by a production crash report.

## Surfaces touched

- The shared deployment pre-flight read that every deployment endpoint
  authorizes through.
- The API's thin PostgREST client, where a non-2xx response becomes an
  exception.
- The deployment orchestration endpoints' error handling, where that
  exception becomes an HTTP answer.
- The app's cross-table embeds — both the API's reads and the web app's
  Supabase reads — wherever the report-inbox junction created a second
  relationship path.
- The API test suite, including its live-database SQL harness.
- The project guidelines, where the convention for embeds is recorded.

## Risks

- **Treating the bug as unfixed.** The reported call site already carries a
  named relationship. Re-applying that same edit and shipping it as the fix
  would close the bug without ever demonstrating a deployment deletes. The
  deliverable is the verified path plus the coverage, not the one-line hint.
- **Naming a constraint couples code to a constraint name.** A later
  migration that drops and recreates the foreign key under a different name
  breaks the read at runtime, with nothing failing at build time. This is
  the reason the regression coverage should assert the named constraints
  exist rather than only that the string is present.
- **Naming the wrong relationship.** A hint that points at a constraint
  going the other direction does not error — it silently returns different
  rows. Each hint added in the sweep has to be checked against the actual
  relationship, not pattern-matched from a neighbouring query.
- **Changing what a failed Supabase call returns.** Endpoints that today
  answer 500 on an internal error would start answering a translated status.
  Existing tests and any UI branch that keys off the status could change
  behaviour; the translation should preserve the caller-visible outcome for
  the cases already covered.
- **An over-eager ambiguity check.** Only a junction with two NOT NULL
  foreign keys produces this; a schema check that flags every table with two
  foreign keys will fail loudly on relationships that work fine, and a noisy
  guard gets disabled.
- **Deleting a deployment silently deletes its app reports.** The reports
  cascade with the row, including reports already promoted to work items —
  the work item survives, but its "reported by the app" panel quietly
  vanishes. This bug came from exactly such a report. Flagged for the
  manager: whether the confirmation should name what goes with it is a
  product decision, not part of the fix.
- **Production still runs whatever was last shipped there.** The report came
  from a deployed instance; until a release carries this to that
  environment, the crash can recur there and look like the fix failed.

## Dependencies

- The report-inbox migration is applied to the live database — the
  ambiguity, and therefore the bug, exists only where it has been applied.
  No new migration is needed: the schema is correct, the queries were
  relying on inference.
- The live-SQL regression coverage depends on the suite's existing
  `DATABASE_URL` harness, which skips where the database is unreachable —
  so the guard must degrade to a skip, never a false pass.
- Nothing must land first, and no other work item blocks this.
- Confirming the fix on the environment that filed the report requires a
  release from the default branch to production; that is a manager action
  after merge, outside this change.
- **Exit bar:** the sweep applied, the coverage authored, and
  `validate_submission` clean. Whether the suite passes is for a worker with
  an environment that can run it to observe and report.

## Approved test plan

# Test plan — BUG-1.1

## What this change deserves, by kind

- **The shared pre-flight read** is the single point every deployment
  operation passes through. It wants route-level coverage that exercises
  more than one operation, so a future regression there fails in several
  places at once rather than hiding until someone clicks delete.
- **The error translation** wants a case built from a real refusal shape —
  a 3xx as well as the 4xx/5xx already thought about — since the whole
  reason this reached a user as "API error 500" is that a redirect status
  was never considered a failure worth naming.
- **The ambiguity guard** wants live-SQL coverage against the real
  relationship graph, rolled back like the suite's other SQL tests, and
  skipping rather than passing where no database is reachable. Asserting on
  a hardcoded list of constraint names would pass forever while the schema
  moves underneath it.

The cases below are the acceptance-level ones a person walks by hand.

```json
[
  {
    "title": "A deployment can be deleted from the project's Deployments tab",
    "steps": "1. Sign in as an org owner and open a project that has at least one non-protected deployment.\n2. Open the Deployments tab.\n3. Click delete on that deployment and confirm in the dialog.\n4. Wait for the list to refresh, then reload the page.",
    "expected_result": "The dialog closes with no error text, the deployment is gone from the list, and it is still gone after the reload. No HTTP error is shown at any point."
  },
  {
    "title": "Running a deployment still works after the fix",
    "steps": "1. Open a project with a deployment configured against a reachable server.\n2. Trigger a run from the Deployments tab.\n3. Watch the run's status.",
    "expected_result": "The run starts and reports progress as before. The operation that shares the same pre-flight read as delete is unaffected."
  },
  {
    "title": "A protected deployment is still owners-only",
    "steps": "1. Sign in as an org member who is not an owner.\n2. Open a project that has a deployment marked protected.\n3. Attempt to delete it.",
    "expected_result": "The delete is refused with the owners-only message, not a generic HTTP error, and the deployment remains in the list. The authorization gate that runs after the pre-flight read is intact."
  },
  {
    "title": "Deleting a deployment that has app reports takes the reports with it and leaves promoted work items standing",
    "steps": "1. Pick a deployment that has at least one report in the reports hub, and note whether any of those reports was promoted to a work item.\n2. Delete that deployment and confirm.\n3. Open the reports hub and filter to that deployment.\n4. Open any work item that was promoted from one of those reports.",
    "expected_result": "The delete succeeds. The deployment's reports no longer appear in the hub. The promoted work item still opens and reads normally — its 'reported by the app' panel is simply absent."
  },
  {
    "title": "The server detail page still names each deployment's project",
    "steps": "1. Open Servers and pick a machine that hosts at least one deployment.\n2. Read the deployments listed on the server's detail page.",
    "expected_result": "Each deployment is listed with its project name filled in, not blank and not an error state — the second place that embeds the same ambiguous pair still resolves."
  }
]
```

## Outcome

Merged `cf72aa9` · [PR #127](https://github.com/Nikesh-Consulting-LLC/software-factory/pull/127) · 2026-07-29

Files changed: .factory-mcp.json, .factory-workspace.json, CLAUDE.md, apps/api/app/main.py, apps/api/app/supabase.py, apps/api/tests/embed_graph.py, apps/api/tests/test_deployments.py, apps/api/tests/test_embed_ambiguity.py, apps/api/tests/test_embed_ambiguity_sql.py, apps/api/tests/test_supabase_errors.py, apps/web/src/app/(app)/projects/[id]/epics/[epicId]/page.tsx
