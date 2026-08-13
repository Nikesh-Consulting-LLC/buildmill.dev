# Performance analysis — 2026-08-12

Why the app feels laggy with a handful of projects, and what breaks first at
100s of projects / 1000s of work items.

Measured against **prod** (`wdudmfhhqxrqzoyhuzwx`) with `pg_stat_statements`
(stats window: 2026-06-30 → 2026-08-12, ~6 weeks) plus static reading of
`apps/web` and `apps/api`. Every number below is observed, not estimated.

## The headline

The app is not slow because of data volume. Prod holds **63 work items**. It is
slow because of **fixed cost per navigation** and **one runaway background
consumer**:

| # | Finding | Measured |
|---|---|---|
| 1 | Realtime WAL decoding is the database | **28,016 s of 31,286 s — 89.5% of all DB execution time**, 2.45 M calls @ 11.4 ms, for **12 live subscriptions** |
| 2 | The app shell re-computes the whole dashboard on every navigation | `loadWaiting` (8 org-wide queries) runs in `layout.tsx` on **every** page, then **again** on `/dashboard` |
| 3 | The work-item list ships every body in the org | `VIEW_ISSUE_SELECT` includes `body` + `acceptance_criteria`, no `limit`, no project scoping |
| 4 | The API opens a new Postgres connection per call | **214** `_connect(settings)` sites, no pool; 143,388 `pgbouncer.get_auth` calls |
| 5 | Worker auth writes on every request | **940,000** `update workers set last_seen_at` — WAL that feeds finding #1 |
| 6 | Nothing ever ages out | `api_request_log` 585 k rows / 106 MB, `content_audit` 36 MB, no retention job anywhere |
| 7 | Index hygiene | 117 unindexed FKs, 27 unused indexes, 1 duplicate index, 3 RLS init-plan re-evaluations |
| 8 | One row is 30 MB | `runs.diff` — 29 MB across the table, `max_diff` = 30,098,163 bytes in a single row |

Findings 2 and 3 explain *today's* lag. Findings 1, 4, 5 explain why it will not
survive growth. Findings 6–8 are the slow leaks behind both.

---

## 1. Realtime is consuming the database

```
tot_ms       calls      mean_ms  query
28,007,104   2,449,775  11.4     SELECT wal->>$5 as type, wal->>$6 as schema, ...
   227,470       1,473  154.4    SELECT name FROM pg_timezone_names
   134,544      19,967   6.7     [PostgREST] select from issues ...   ← highest app query
```

The WAL-to-JSON decoder (Realtime's per-subscriber RLS evaluation) costs **123×
more than the next statement** and **8.5× more than everything else in the
database combined**. It is doing this for 12 subscriptions.

Two causes, both in our control:

**The publication is too wide — 27 tables**, including high-churn audit tables
nobody subscribes to at scale:

```
agent_pool_placement_requests, agent_server_jobs, agent_servers,
agent_session_events, agent_slots, clarifications,
dashboard_incident_dismissals, deployment_run_events, deployment_runs,
documents, issue_comments, issues, notifications, release_prep_runs,
release_test_results, releases, run_activity, run_item_commits, run_trace,
runner_command_audit, runner_incidents, runner_sessions, runs,
suite_run_events, suite_runs, workspace_prep_jobs
```

Every insert into `agent_server_jobs` (9,125 rows), `run_trace` (6,968) and
`runner_command_audit` (1,645) is decoded and RLS-checked **per subscriber**,
whether or not any client cares.

**Subscriptions are unfiltered.** `shell-live-count.tsx` does it right
(`filter: org_id=eq.${orgId}`). [`use-project-issues.ts:57`](../apps/web/src/app/(app)/issues/use-project-issues.ts#L57) does not:

```ts
.on("postgres_changes", { event: "*", schema: "public", table: "issues" }, ...)
```

Every issue change in **every org** is decoded, RLS-evaluated, and shipped to
every hub subscriber, which then discards most of it in JS. At 100s of projects
this is the firehose that never stops.

## 2. The shell re-computes the dashboard on every navigation

[`apps/web/src/app/(app)/layout.tsx`](../apps/web/src/app/(app)/layout.tsx) wraps every page in the app and runs, strictly sequentially:

1. `auth.getUser()` — network round trip
2. `principals` → `must_change_password`
3. `profiles` → `display_name, avatar_url`
4. `principals` → `id` ← **same table, same key as step 2**
5. `resolveActiveOrg()` → `organization_members` + `principals` ← **third read of `principals`**
6. **`loadWaiting(supabase, orgId)`** — 8 org-wide queries over `issues`,
   `releases`, `runs`, `clarifications`, `guideline_recommendations`,
   refreshes, parked runs, dispatch blocks — **used only for `.pendingCount`**, a
   sidebar badge number
7. `notifications` (limit 30)

That is ~6 sequential round trips plus an 8-query fan-out before any page's own
data starts, on **every navigation**. `principals` is read three times by the
same key. No page can render faster than this floor.

Then `/dashboard` calls `loadThingsToDo`, whose first line is
`await loadWaiting(supabase, orgId)` — **the same 8 queries a second time**.

`loadWaiting`'s `issues` query is the most-executed application statement in the
database (19,967 calls, 134 s) — that is the badge.

There is **zero use of React `cache()`** anywhere in `apps/web` (0 occurrences),
so nothing dedupes within a request.

**This is the fixed cost that makes the app feel laggy with 3 projects.** It
also scales linearly with org size — `loadWaiting` is org-wide and unbounded, so
it gets worse with every project added.

## 3. The work-item list ships every body in the org

[`apps/web/src/app/(app)/issues/page.tsx:76`](../apps/web/src/app/(app)/issues/page.tsx#L76) states the design plainly:

> "The hub always loads every project in the active org so any stored/typed
> selection has its data ready; the client narrows further to the selection."

The query has **no `.limit()`**, **no project scoping** (it passes every project
id in the org), and selects:

```ts
export const VIEW_ISSUE_SELECT =
  "id, title, status, type, updated_at, github_issue_number, github_issue_url,
   project_id, parent_id, epic_id, item_no, sub_no, complexity,
   body, acceptance_criteria, epics(title, number)";
```

`body` and `acceptance_criteria` are **never rendered by any list view**. They
are fetched solely so the client-side realtime filter (`issueMatchesQuery`) can
match text the server has already filtered on. Measured:

```
issues: 63    body: 55 kB    acceptance_criteria: 17 kB    avg body: 902 B    max: 12,015 B
```

At 5,000 work items that is roughly **4.5 MB of markdown** serialized into the
RSC payload, shipped to the browser, parsed, and held in React state on every
`/issues` load — for a search predicate that runs server-side anyway.

Compounding it: **no virtualization anywhere** (no `react-window`, no
`@tanstack/virtual` in `package.json`) and no pagination on the hub. Outline,
Board and Table each render every row.

## 4. The API has no connection pool

[`apps/api/app/db.py:100`](../apps/api/app/db.py#L100) — `_connect()` calls `psycopg.connect()` directly, and there
are **214 call sites**. Every db helper opens a fresh connection, queries,
commits, closes. A request handler that calls three helpers pays three
connection handshakes to Supabase.

`SELECT * FROM pgbouncer.get_auth($1)` at **143,388 calls** is the direct
evidence of that churn.

Related: [`record_api_request`](../apps/api/app/db.py#L111) opens **its own** connection for a single
insert, 584,613 times. It is correctly off the response path
(`asyncio.create_task` + `to_thread` in [`main.py:260`](../apps/api/app/main.py#L260)) — so it does not add
latency — but it doubles connection count and consumes a threadpool slot per
request, which under load starves every other synchronous DB call.

## 5. Worker auth writes on every request

[`get_worker_by_token`](../apps/api/app/db.py#L1642) — "the single auth path for every `/worker/*` call and the
git remote" — is an `UPDATE`:

```sql
update public.workers set last_seen_at = now() where token_hash = %s and status = 'active'
```

Two variants, **414,305 + 525,672 = ~940,000 executions**. Every one produces a
WAL record, which feeds finding #1, and takes a row lock that serializes a
worker's concurrent requests. A presence timestamp does not need write
durability on the authentication path.

## 6. Nothing ages out

No retention logic in the API, and **no `pg_cron` schedule in any migration**.

| Table | Rows | Size |
|---|---|---|
| `api_request_log` | 584,934 | **106 MB** |
| `content_audit` | 31,260 | 36 MB |
| `runs` | 185 | **33 MB** |
| `agent_server_jobs` | 9,125 | 3.6 MB |
| `run_trace` | 6,968 | 2.1 MB |
| `runner_command_audit` | 1,645 | 3.0 MB |

`api_request_log` is a diagnostic table (US-62.8) growing at ~585 k rows per six
weeks with one index. `client_perf_events` takes a client-side insert on every
page load via `web-vitals-reporter.tsx` and has the same problem.

## 7. Index and policy hygiene

From Supabase's performance advisor (169 findings):

- **117 unindexed foreign keys** — worst offenders `documents` (5), `app_issues`
  (4), `clarifications` (4), `guideline_refreshes` (4), `issue_comments` (4),
  `runs` (4), `test_cases` (4). Every one makes a cascading delete or a
  parent-scoped filter a sequential scan.
- **27 unused indexes** — pure write amplification.
- **1 duplicate index** — `projects_id_org_key` and `projects_id_org_unique` are
  identical.
- **3 `auth_rls_initplan`** — `issue_comments`, `client_perf_events`,
  `user_activity_sessions` re-evaluate `auth.uid()` **per row** instead of once.
  (135 policies total; 12 unwrapped `auth.*()` calls found by direct query.)
- **20 multiple-permissive-policies** — `app_issues` (10), `organization_members`
  (5), `profiles` (5). Postgres evaluates *every* permissive policy on every row.

Missing for our actual access pattern: `issues` has `issues_project_idx
(project_id)` and `issues_active_idx (project_id) WHERE abandoned_at IS NULL`,
but the hub orders by `updated_at DESC` — there is no
`(project_id, updated_at DESC)`, so every hub load sorts the whole set.

## 8. A 30 MB row

`runs.diff` holds 29 MB across the table, with `max(pg_column_size(diff)) =
30,098,163` — one run's diff is 30 MB of text inline in `runs`. No web or API
query currently does `select *` on `runs`, so this is not a hot path today, but
it makes `runs` 33 MB for 185 rows, bloats every backup, and turns the first
careless `select *` into a 30 MB response.

---

## What to fix, in order

Ranked by measured impact per unit of work:

1. **Shrink the realtime publication + filter every subscription** — attacks
   89.5% of database time.
2. **Make the shell cheap** — `React.cache()` for request-level dedup, a count
   query instead of `loadWaiting`, parallel round trips. Attacks the fixed
   per-navigation floor every user feels.
3. **Scope and slim the work-item list** — drop `body`/`acceptance_criteria`,
   scope to selected projects, paginate, virtualize. This is the one that
   *fails* rather than slows at 1000s of items.
4. **Pool the API's connections** and stop writing on worker auth.
5. **Retention + index hygiene** — cheap, mechanical, compounding.

Phase 87 (`us-87.1` … `us-87.9`) turns this list into stories.


---

## Post-release measurement (2026-08-13) — a correction to the framing above

Phase 87's first seven stories shipped to prod on 2026-08-13. Two of the
headline claims now have live numbers behind them, and one needs a correction.

**Proven live:**

- **The connection pool works.** 47,650 `api_request_log` rows written after
  the deploy, latest 7 seconds old — so `psycopg_pool` opened real connections
  through Supabase's pooler under real traffic for hours, which no test could
  show (Essential blocks outbound network by design).
- **The heartbeat throttle works.** 49,970 worker authentications produced
  5,582 `last_seen_at` writes — **~9x fewer**, matching the 15-second interval.
- **Nothing broke.** Zero 5xx since the deploy. The ~50% 401 rate on
  `/worker/pool` and `/worker/release-prep` is pre-existing: the 200/401 split
  is identical before (50.2% ok) and after (51.4% ok) the change.

**The correction — finding 1's share is real, its magnitude is not what
"89.5%" implies.** A 51-second live sample measured the WAL decoder at 1.86
calls/s and 18.9 ms/s of database time, against 20.7 ms/s for the whole
database. So the share is still ~91% — but the *absolute* cost is **1.9% of
one core**, and the six-week figure works out to **0.75% of one core**.

Realtime dominates because the database is otherwise close to idle, not
because it is saturating anything. The share was reported accurately; the
framing ("Realtime is consuming the database") implied a saturation the
absolute numbers do not support. That does not make the trim wrong — it is
still the largest single consumer, it grows with every published table and
every unfiltered subscriber, and it was doing that work for twelve
subscriptions. But for the symptom actually reported — *"laggy with just a few
projects"* — findings 2 and 3 are the cause, as the original ranking said, and
finding 1 is a scaling risk rather than a present-tense bottleneck.

**Still not measured:** us-87.5's AC6 asks for a before/after across a
representative day, which needs a `pg_stat_statements` reset. A 51-second
sample cannot show whether the publication trim and the subscription filters
reduced the rate — it is far too short a window, and comparing an instantaneous
rate against a six-week average is not valid either. That measurement is
outstanding.
