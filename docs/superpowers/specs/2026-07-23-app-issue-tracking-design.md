# Application Issue Tracking — Design

**Date:** 2026-07-23
**Status:** Approved in brainstorm; stories written, **not built**
**Phase:** 16 — Application issue tracking

> The design and its seven stories sat on a side branch from 2026-07-23 until they were brought
> onto `main` on 2026-07-28 so the phase is visible in the build sequence. Migration numbers
> below were renumbered from 119 to the next free number at that time (182). Nothing here has
> been implemented.

## Context

Every app Build Mill deploys runs unattended once it's live — nobody is watching it for crashes,
and the app's own users have no way to tell the factory something is wrong short of emailing
someone. Today the only way a bug reaches the factory is a manager noticing it themselves and
typing up a work item by hand.

This phase gives every **deployment** (not project — see the locked decision below) a way to
report problems straight into Build Mill, from two directions:

1. **Automated** — the deployed app's own error handler catches an unhandled exception and posts
   it to Build Mill.
2. **User-submitted** — a person using the deployed app hits a "Report an issue" widget and sends
   a description straight to Build Mill.

Both land in a new inbox the manager triages, separate from the existing work-item pipeline.
Triage is a manager decision: what looks like a real bug gets **promoted** into a normal `issues`
row (`type=bug`) and flows through the factory's existing plan → code → review pipeline exactly
like any other bug. What isn't a real bug gets ignored, staying out of the work-item pipeline
entirely.

### Locked decisions (from the 2026-07-23 brainstorm)

1. **App-side error handler**, not external monitoring. A small SDK embedded in the deployed app
   decides what's an error and reports it — Build Mill does not poll or health-check for bugs.
   External monitoring is a possible future addition; the ingestion contract doesn't preclude it.
2. **A "public-ish" key**, not a Vault-tier secret. Modeled on the existing worker-token pattern
   (hashed for lookup, but **revealable**) rather than a write-only Vault secret — it's meant to
   ship inside a client-side bundle, so treating it as unrecoverable would just make manager setup
   worse without buying real security. Abuse is bounded by rate-limiting and scope, not secrecy.
3. **Scoped per deployment, not per project.** A project's UAT and Production deployments get
   *separate* keys. This refines the original framing ("unique endpoint per project") — a
   deployment, not a project, is the thing with a live URL an app-side SDK actually points at, and
   keeping UAT/Prod reports separate means a manager can tell which environment is on fire.
4. **API + embeddable widget** for user reports, not API-only. Build Mill ships a drop-in
   feedback-widget script so a project's own developers don't have to build a report UI — same
   ingestion endpoint underneath, different payload shape.
5. **Triage promotes into the real pipeline.** Confirming a report as a bug creates a genuine
   `issues` row and hands off to the existing dispatch/plan/code/review flow — this phase does not
   invent a second pipeline, only a front door and an inbox.
6. **Automated reports are deduplicated by fingerprint.** The same crash repeating a thousand
   times increments one row's occurrence count rather than creating a thousand rows.
7. **Triage happens in a new top-level, cross-project hub** ("Reports"), not a per-project tab —
   consistent with how Work Items (Phase 8) already reads across every project at once.

## Goals

- A deployed app can report an unhandled error to Build Mill with a few lines of SDK setup, no
  manual API work.
- A deployed app's own users can submit a free-text issue through a drop-in widget.
- Both land in a per-deployment inbox, deduplicated, without ever touching the `issues` table
  until a manager says so.
- A manager triages from one cross-project hub and promotes a real bug into the existing pipeline
  in one action, prefilled with everything the report captured.
- The ingestion endpoint is public-reachable (deployed apps aren't Build Mill sessions) but scoped
  and rate-limited so it can't be used to spam a project's data or someone else's org.

## Non-goals (this phase)

- External/synthetic monitoring (uptime checks, log scraping) — locked decision #1 keeps this out;
  a future story could add it against the same ingestion contract.
- Non-JS SDKs (Python/server-side error handlers for non-Node backends). The embeddable SDK ships
  for browser + Node first; the HTTP contract is documented so another language can integrate by
  hand, but a native SDK for another runtime is a follow-up.
- Automatic duplicate-detection *across* fingerprints (e.g. clustering similar-but-not-identical
  stack traces) — dedup is exact-fingerprint only.
- Screenshots / session replay attachments — `context` is a free-form JSON bag the SDK can put
  data into, but capturing and uploading a screenshot is not built this phase.
- Reopening a promoted report if the linked work item is later abandoned — one-way promotion only.

## Architecture

```
 deployed app (anywhere)                          Build Mill server
┌─────────────────────────────┐                ┌───────────────────────────────────┐
│  error-capture SDK           │   HTTPS POST   │  api — FastAPI                     │
│  (window.onerror /            │───────────────▶│  POST /api/v1/report/{deployment}  │
│   unhandledrejection /        │  X-Report-Key   │   /issues   (public, key-auth)     │
│   process.on('uncaught...'))  │                │        │                            │
│                               │                │        ▼                            │
│  feedback widget               │   HTTPS POST   │  fingerprint + dedupe + rate-limit  │
│  ("Report an issue" button)   │───────────────▶│        │                            │
└─────────────────────────────┘                │        ▼                            │
                                                │  app_issues (service-role insert)   │
                                                │        │                            │
                                                │        ▼  manager triages            │
                                                │  Reports hub (/reports, web UI)     │
                                                │        │  "Promote to work item"      │
                                                │        ▼                            │
                                                │  issues (type=bug) — normal pipeline │
                                                └───────────────────────────────────┘
```

Two new client-facing surfaces (SDK, widget), one new public ingestion endpoint, one new table,
one new triage hub, one promotion path into the existing pipeline. Nothing here changes how
`issues`/`runs`/approvals already work — a promoted report is just a normal work item from that
point on.

### Data model

**`app_issues`** (new table, migration 182) — one row per distinct problem, not per occurrence:

| Column | Notes |
|---|---|
| `id`, `org_id`, `project_id`, `deployment_id` | org-scoped like every other table; `deployment_id` is the reporting scope |
| `source` | `automated` \| `user_report` |
| `fingerprint` | `sha256(error_type + normalized_message + top_3_frames)`, null for `user_report` |
| `occurrence_count`, `first_seen_at`, `last_seen_at` | repeats on the same fingerprint increment these instead of inserting |
| `title` | error message (automated) or the reporter's subject line (user report) |
| `message`, `stack_trace` | free text, nullable |
| `context` | jsonb — URL, user agent, app version, arbitrary SDK-supplied metadata |
| `reporter_name`, `reporter_email` | `user_report` only, both nullable (anonymous allowed) |
| `status` | `new` → `triaged` \| `promoted` \| `ignored` |
| `promoted_issue_id` | FK to `issues`, set on promotion |
| `triaged_by`, `triaged_at` | manager who acted, and when |
| `created_at`, `updated_at` | |

RLS: standard `is_org_member(org)` policy for manager reads/writes (triage). **Inserts never go
through RLS** — the ingestion endpoint writes via `api`'s service-role connection after validating
the deployment key itself, the same carve-out `APPLICATION.md` already documents for anything
needing a secret or a cross-org-safe write path.

**`deployments`** gains four columns rather than a new join table, since the key is 1:1 with a
deployment: `issue_reporting_enabled` (bool, default false — opt-in), `issue_report_key_hash`,
`issue_report_key_last4`, `issue_report_key_vault_secret_id`. This mirrors `workers.token_hash` /
`token_last4` / `vault_secret_id` exactly, including a `security definer`
`reveal_deployment_report_key(deployment)` RPC scoped to org members — the key is meant to be
copy-pasted into a config, same reasoning `APPLICATION.md` already gives for worker tokens (US-14.7).

### Ingestion endpoint

`POST /api/v1/report/{deployment_id}/issues` — new router, mounted under `/api/v1` but **not**
behind Supabase JWT auth (a deployed app has no manager session). Auth is a header,
`X-Report-Key: <key>`, checked by hashing and comparing against `deployments.issue_report_key_hash`
for that `deployment_id`. An invalid key or an unknown/disabled deployment both answer a generic
`401` — never distinguishing "wrong key" from "deployment doesn't exist," so the endpoint can't be
used to enumerate deployment IDs.

Request body (both sources share one shape; `source` picks the branch):

```json
{
  "source": "automated",
  "error_type": "TypeError",
  "message": "Cannot read properties of undefined",
  "stack_trace": "...",
  "severity": "error",
  "context": { "url": "...", "user_agent": "...", "app_version": "..." }
}
```

```json
{
  "source": "user_report",
  "title": "Checkout button does nothing",
  "message": "...",
  "reporter_name": "...",
  "reporter_email": "...",
  "context": { "url": "..." }
}
```

Behavior: `automated` reports compute a fingerprint and upsert (increment `occurrence_count` /
`last_seen_at` on a match against a non-terminal row, insert otherwise); `user_report` always
inserts a fresh row. Payload size is capped (stack traces and `context` truncated past a fixed
limit) and the endpoint is rate-limited per deployment key (a generous per-minute cap — enough for
a real incident's error burst, tight enough that a misbehaving client can't flood the table).
Response is minimal (`{id, status}`, `201`) — no internal state leaks back to a public endpoint.

### Client-side pieces

Two independent, dependency-free JS files served statically (not part of the Next.js app router,
since they run inside *other people's* deployed apps, which may not even be JS-built by Build
Mill):

- **Error-capture SDK** — a `<script>` tag configured with `data-deployment` + `data-key`, installs
  `window.onerror` / `unhandledrejection` listeners (browser) and documents the equivalent
  `process.on('uncaughtException')` hook for a Node backend. Fires-and-forgets a POST per error;
  never throws back into the host app if the report itself fails.
- **Feedback widget** — same config shape, renders a small "Report an issue" trigger + form,
  posts `source: "user_report"`.

Both are handed their exact embed snippet (deployment id + key pre-filled) from the deployment's
own page in Build Mill, next to where `website_url` and health checks already live.

### Triage & promotion

`/reports` — new top-level nav item, cross-project (multi-select project filter, reusing the
Work Items hub's pattern from Phase 8). Lists every `app_issues` row grouped by
project → deployment, with source, occurrence count, first/last seen, and status. A detail panel
shows the full stack trace / context JSON / reporter info. Actions: **Promote to work item**,
**Ignore**, **Reopen**.

Promotion is a `security definer` Postgres function, `promote_app_issue(p_app_issue, p_epic_id)`:
creates an `issues` row (`type=bug`, title/description prefilled from the report, stack trace
appended), sets `app_issues.status='promoted'`, `promoted_issue_id`, `triaged_by`/`triaged_at` —
one transaction, mirroring how `dispatch_issue` and `approve_run` already bundle a multi-table
transition into one RPC rather than sequential client-side writes.

## Error handling

- **Ingestion endpoint down or key wrong**: the SDK/widget must never throw inside the host app —
  every failure is caught and swallowed client-side (best-effort reporting, not a hard dependency).
- **Duplicate storms**: fingerprint dedup collapses repeats into one row's counter; rate limiting
  is the backstop for genuinely novel errors arriving too fast (e.g. a crash loop producing a new
  stack trace each time due to embedded timestamps — a known dedup edge case, mitigated by
  normalizing the message before hashing, not solved outright).
- **Disabled deployment**: if `issue_reporting_enabled` is false, the endpoint answers the same
  generic `401` as a bad key — a manager can kill ingestion instantly without rotating the key.
- **Promotion race**: `promote_app_issue` is guarded so a report already `promoted`/`ignored`
  can't be promoted twice; the RPC checks and raises rather than silently double-creating an issue.

## Testing

- Live SQL tests for `promote_app_issue`, the reveal RPC, and fingerprint-dedup upsert behavior
  against a real database (this repo's standing convention — no mocks for RPC-level correctness).
- `pytest` coverage for the ingestion endpoint: valid key accepted, invalid/disabled key rejected
  generically, dedup increments vs. inserts correctly, payload-size and rate limits enforced.
- Manual browser verification of the SDK against a real thrown error and the widget's submit flow,
  plus the Reports hub's list/detail/promote loop end to end.
