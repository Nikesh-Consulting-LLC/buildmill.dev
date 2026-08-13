# Phase 2 — Issue hierarchy & delivery artifacts — Plan Set Overview

> **For agentic workers:** This is an index, not an executable plan. Each story below has its own plan document. Execute them **in the order listed** using superpowers:subagent-driven-development.

**Goal:** Replace `tasks` with a typed `issue` hierarchy, and hang the delivery artifacts (PRD, plan, test plan, approvals, epics, release records) off it — so a feature can be traced from raw idea to production with an answer to "why did we ship this".

**Why one plan per story:** Each of the nine stories produces working, testable software on its own and is independently reviewable. A single Phase-2 mega-plan would not be — it would gate all value behind one merge. The build order below is the index order in [`stories/users.md`](../../../stories/users.md), and it is a real dependency chain, not a preference.

## The plans

| Order | Story | Plan | Migration | Shape |
|---|---|---|---|---|
| 1 | [us-2.1](../../../stories/us-2.1-issues-replace-tasks.md) | [plan](2026-07-14-us-2.1-issues-replace-tasks.md) | `031` | Schema + repo-wide rename. **Foundation — everything else assumes it.** |
| 2 | [us-2.2](../../../stories/us-2.2-typed-issue-creation.md) | [plan](2026-07-14-us-2.2-typed-issue-creation.md) | `032`? | Typed create/edit + filters |
| 3 | [us-2.3](../../../stories/us-2.3-feature-prd.md) | [plan](2026-07-14-us-2.3-feature-prd.md) | `033`? | LLM-drafted PRD + approve gate |
| 4 | [us-2.4](../../../stories/us-2.4-story-breakdown.md) | [plan](2026-07-14-us-2.4-story-breakdown.md) | `034`? | LLM split into child stories |
| 5 | [us-2.5](../../../stories/us-2.5-plan-runs.md) | [plan](2026-07-14-us-2.5-plan-runs.md) | `035`? | Two-phase dispatch. **Most complex.** |
| 6 | [us-2.6](../../../stories/us-2.6-test-plan-merge-gate.md) | [plan](2026-07-14-us-2.6-test-plan-merge-gate.md) | `036`? | Materialize test cases + soft gate |
| 7 | [us-2.7](../../../stories/us-2.7-approval-log-audit.md) | [plan](2026-07-14-us-2.7-approval-log-audit.md) | none | Surfacing only — a query, not a store |
| 8 | [us-2.8](../../../stories/us-2.8-epics.md) | [plan](2026-07-14-us-2.8-epics.md) | none? | Epic CRUD + rollup |
| 9 | [us-2.9](../../../stories/us-2.9-release-records.md) | [plan](2026-07-14-us-2.9-release-records.md) | `039`? | Post-merge journey + sign-off gates |

Migration numbers after `031` are provisional: each plan claims the next free number **at the time it is executed**. If stories are reordered, renumber — never reuse.

## Global Constraints

These apply to **every** task in **every** Phase-2 plan. They are copied from [CLAUDE.md](../../../CLAUDE.md); that file wins if it changes.

- **No change without a story.** If implementation reveals a story is wrong, fix the story **in the same change** — story file, its row in `stories/users.md`, and its folder stay in sync.
- **Migrations are two-place**: the numbered file in `infra/supabase/migrations/NNN_name.sql` **and** applied live to Supabase project `Software-Factory` (`wdudmfhhqxrqzoyhuzwx`) via MCP `apply_migration`, then `apps/web/src/lib/supabase/database.types.ts` regenerated. A written-but-unapplied migration makes correct code look broken.
- **RLS on every table**, org-scoped via `public.is_org_member(org_id)`. Cross-org isolation verified for every new table.
- **Composite `(id, org_id)` FKs**, not plain FKs — FK validation bypasses RLS, so a plain FK lets an attacker reference another org's ids. See `020_deployments.sql:7-11` for the rationale.
- **Append-only tables** (`issue_events`, `approvals`, `release_record_events`) get `select` + `insert` policies only. No update, no delete.
- **Stored functions** are SECURITY INVOKER with `revoke execute ... from public, anon` then `grant execute ... to authenticated`. Authorization is RLS.
- **Build less API**: plain CRUD goes through the Supabase JS SDK under RLS directly from `apps/web`. FastAPI is only for orchestration — dispatch, runner callbacks, GitHub operations, LLM calls.
- **Secrets are write-only.** Never add a policy, view, endpoint, log line, or signed URL that returns key material.
- **shadcn/ui here is Base UI, not Radix**: triggers use `render={<Button />}` not `asChild`; pass `items` to `Select`.
- **Shared UI**: use `StatusBadge` (`status-badge.tsx`) and `EmptyState` (`empty-state.tsx`). Don't invent new patterns per page.
- **Next.js 16**: `src/proxy.ts` (exported `proxy`) replaces `middleware.ts`. Read `node_modules/next/dist/docs/` before assuming App Router APIs match training data.
- **Never commit secrets.**

## Testing reality — read this before writing any test step

The pytest suite in `apps/api/tests/` is **monkeypatched unit tests against FastAPI with no live database**. `test_dispatch.py` patches `app.routers.tasks.rpc` and `app.routers.tasks.postgrest_get` and asserts on HTTP status codes and payloads. It does **not** exercise Postgres.

Consequences every plan must respect:

- **Do not write pytest that asserts RLS, CHECK constraints, triggers, or SQL behavior** — it cannot run. Verify those with SQL against the live project (MCP `execute_sql`) and record the result in the task.
- **`apps/web` has no component test runner.** Frontend verification is `npm run build` (which includes the type check) plus a concrete manual click-through. Do not invent a test framework the repo does not have.
- pytest verifies the **API contract**: status codes, request/response shape, error mapping (`RpcError('... not found')` → 404, `'not dispatchable'` → 409, missing token → 401).

## Verification commands

```bash
npm run build      # from repo root — production build + type check
npm run lint       # eslint via apps/web
cd apps/api && .venv/Scripts/python -m pytest -q    # API suite
```

## Phase 2 exit test

Take one real feature from raw idea → approved PRD → stories → approved plan → code → tested → merged → released, and answer "why did we ship this" entirely from the approval log and release record.
