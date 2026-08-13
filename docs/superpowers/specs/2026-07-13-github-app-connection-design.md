# US-1.19 — Connect GitHub account(s) and repos — Design

**Status:** Approved (brainstorming complete, ready for implementation planning)

## Summary

Replace the free-text `owner/name` repo field and static `GITHUB_TOKEN` with a real GitHub App connection per org. A manager installs a platform-level GitHub App into their GitHub account/org (choosing repos via GitHub's own install UI), `api` stores the resulting installation id, and everything that needs GitHub access (repo picker, PR/Projects-v2 reads, PR merge, PR revert) mints a short-lived installation token per request instead of using a shared static token.

## Scope decisions (from brainstorming)

- **GitHub App bootstrap is manual, one-time, operator-level** — not a feature we build code for. The App is a platform-level singleton (one App, many org installations), created once via `github.com/settings/apps/new` following the checklist below, with credentials dropped into `apps/api/.env`.
- **Runner-side (US-1.15) wiring is deferred.** `provider_claude.py` doesn't exist yet — this story does not touch `apps/runner`. When US-1.15 lands, its `gh` CLI auth should request a short-lived installation token from `api`; that's a note for that story's own plan, not code here.
- **GitHub Projects (v2) boards are in scope**, read via GraphQL alongside open PRs, in a new "GitHub" tab on the project detail page (reusing the `Tabs` component from US-1.18).
- **Revert uses GraphQL** (`revertPullRequest` mutation), not REST — GitHub's REST API has no revert-PR endpoint; the story text's `POST /repos/.../pulls` phrasing was aspirational/inaccurate and is corrected here.
- **Repo picker access-scoping is validation.** No separate "does this repo exist" check on save — selecting from a list fetched via the installation token *is* the proof of access.

## Data model

`infra/supabase/migrations/010_github_installations.sql`, mirroring the `project_guidelines`/`llm_settings` org-scoped RLS pattern:

```sql
create table public.github_installations (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  installation_id bigint not null unique,
  account_login text not null,
  account_type text not null check (account_type in ('User', 'Organization')),
  connected_by uuid not null references auth.users(id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index github_installations_org_idx on public.github_installations (org_id);

alter table public.github_installations enable row level security;

create policy "members manage their org github installations"
  on public.github_installations for all
  using (public.is_org_member(org_id))
  with check (public.is_org_member(org_id));

create trigger github_installations_updated_at
  before update on public.github_installations
  for each row execute function public.touch_updated_at();
```

Only credential material stored is the numeric `installation_id`. The App's private key never enters Supabase — it's `apps/api/.env` config only.

## GitHub App bootstrap (manual, one-time — operator does this, not code)

1. Go to `github.com/settings/apps/new`.
2. **GitHub App name**: e.g. `Software Factory (dev)`. **Homepage URL**: the web app's base URL.
3. **Webhook**: uncheck "Active" — no webhook URL/secret needed (webhook-driven sync is out of scope).
4. **Setup URL**: `{API_BASE_URL}/api/v1/github/install/callback`, and check "Redirect on update" so re-installs/permission changes also hit it.
5. **Permissions**:
   - Repository → Contents: **Read & write** (needed later by US-1.15's clone/push; harmless to grant now since only the App owner, not orgs, sees this screen).
   - Repository → Pull requests: **Read & write** (list, merge, revert).
   - Repository → Metadata: **Read** (mandatory default).
   - Organization → Projects: **Read** (Projects v2 GraphQL reads).
6. **Where can this GitHub App be installed?**: "Any account" (so any org/user can install it, not just the App's own owner).
7. Create the App. Note the **App ID** and **App slug** (from the URL). Generate a **private key** (downloads a `.pem`).
8. Add to `apps/api/.env`:
   ```
   GITHUB_APP_ID=<app id>
   GITHUB_APP_SLUG=<app slug>
   GITHUB_APP_PRIVATE_KEY=<full contents of the .pem, PEM-armored (multi-line env value)>
   GITHUB_APP_STATE_SECRET=<random 32+ byte secret, e.g. `openssl rand -hex 32`>
   ```

## Connect / disconnect flow

**Connect** — a plain link in the Settings → GitHub card, no API call to start:
```
https://github.com/apps/<GITHUB_APP_SLUG>/installations/new?state=<signed-state>
```
`<signed-state>` is a stateless HMAC-signed token (`org_id` + short expiry + nonce, signed with `GITHUB_APP_STATE_SECRET`) — no DB row needed for it, just CSRF/intent protection. GitHub renders its own install UI; the manager picks the account and which repos to grant (this **is** the repo-access picker — not rebuilt).

GitHub redirects to the Setup URL, a new **unauthenticated** FastAPI endpoint:
```
GET /api/v1/github/install/callback?installation_id=...&setup_action=install&state=...
```
Steps: verify state signature + expiry → extract `org_id` → call GitHub (App-JWT-authenticated) `GET /app/installations/{id}` to confirm it's real and read `account.login`/`account.type` → upsert `github_installations` (on `installation_id` conflict, update `org_id`/`account_login`/`connected_by` — handles reinstall-under-different-org edge case by simply taking the latest) → 302 redirect to `{WEB_BASE_URL}/settings?github=connected` (or `?github=error` on any failure, never a raw 500 to the browser mid-redirect).

**Disconnect** — authenticated `POST /api/v1/github/installations/{id}/disconnect`: RLS-scoped lookup (cross-org rows are invisible, same as every other org-scoped endpoint) → `DELETE /app/installations/{id}` (App-JWT-authenticated, actually uninstalls on GitHub's side) → delete the row.

**Settings UI**: new "GitHub" `Card` on the settings page, parallel to the existing "LLM provider" card. Shows connected installation(s) (`account_login`, `account_type`) with a "Disconnect" button per row (confirmation, since it uninstalls on GitHub too), or a "Connect GitHub" link/button when none exist. Reads a `?github=connected|error` query param on load to show a one-time toast/banner (no toast library in this codebase — inline banner text instead, matching existing "no toast" convention).

## Token minting

New `apps/api/app/github.py` module — the one place all GitHub calls route through:

- `mint_app_jwt(settings) -> str`: RS256 JWT via PyJWT (already a dependency), `iss=GITHUB_APP_ID`, 10 minute expiry, signed with `GITHUB_APP_PRIVATE_KEY`.
- `mint_installation_token(settings, installation_id) -> str`: `POST /app/installations/{id}/access_tokens` with the App JWT as bearer, returns the ~1hr installation token. **Minted fresh per request** — no cache/store, matches the story's "minted per-request" wording and is the simplest correct thing given tokens are short-lived and call volume is low.
- Thin wrappers for the actual GitHub REST/GraphQL calls used elsewhere (`list_installation_repos`, `list_open_pulls`, `list_projects_v2`, `get_pull`, `revert_pull_request`, `merge_pull_request`, `uninstall`) — all plain `httpx`, no new SDK dependency, consistent with `supabase.py`'s existing "thin client" style.

## Repo picker + Project dialog integration

**`GET /api/v1/github/repos`** (JWT-protected): looks up the caller org's `github_installations` row(s) via RLS, mints a token per installation, calls `GET /installation/repositories` (paginated) for each, merges results into `[{full_name, default_branch}]`.

**`project-dialog.tsx`**: the free-text `repo_full_name` `Input` is replaced with a `Select` populated by this endpoint (client-side `apiFetch`). Selecting a repo also captures `default_branch` from the response, pre-filling that field (still editable). If the org has no installation, the dialog shows an inline prompt linking to Settings → GitHub, matching the "configure your provider first" pattern already used for LLM settings elsewhere in the app. Saving still writes plain `repo_full_name`/`default_branch` to `projects` — **no `projects` schema change**.

## GitHub tab (project detail page)

New tab next to Overview/Guidelines, using the `Tabs` component from US-1.18. Server component fetching two new thin endpoints:

- `GET /api/v1/github/repos/{owner}/{repo}/pulls` → `GET /repos/{owner}/{repo}/pulls?state=open` via installation token → `[{number, title, author, url, updated_at}]`.
- `GET /api/v1/github/repos/{owner}/{repo}/projects` → GraphQL `repository(owner,name){ projectsV2(first:20){ nodes { id title url } } }` via installation token → `[{id, title, url}]`.

Both are on-demand reads, no caching table, no webhook sync (matches "mirrored on demand" + explicit out-of-scope). Two simple lists in the tab; `EmptyState` for zero results in either.

## Merge flow swap (US-1.12's `reviews.py`)

`_merge_pr` token preference order changes:
1. Look up the run's org's `github_installations` row (RLS-scoped `postgrest_get`).
2. If found: mint an installation token, use it as the merge call's bearer token.
3. If not found: fall back to `settings.github_token` (today's behavior, unchanged).
4. If neither: existing `409 "GITHUB_TOKEN not configured — cannot merge a real PR"`.

No behavior change for orgs that haven't connected GitHub yet.

## Revert action

GitHub's REST API has **no** revert-PR endpoint — confirmed via the GitHub changelog: the real mechanism is the GraphQL `revertPullRequest` mutation (added Jan 2023). Design:

- New endpoint `POST /api/v1/tasks/{task_id}/revert`: find the task's most recent `merged`-status run (RLS-scoped) → parse `owner/repo/number` from `run.pr_url` → if `pr_url` starts with `simulated://`, `409` (nothing real to revert) → mint an installation token for the org → resolve the PR's GraphQL node id (`GET /repos/{owner}/{repo}/pulls/{number}`, includes `node_id`) → `revertPullRequest(input: {pullRequestId, title})` via `POST /graphql` → return the new PR's URL.
- Record a `task_events` row (`type: 'reverted'`, `payload: {revert_pr_url}`). **No task status change** — task stays `merged`; the revert is a new PR the manager still reviews/merges separately, matching GitHub's own "Revert" button behavior.
- UI: new button on the task detail page, visible when `task.status === 'merged'`, following the `DispatchButton`/`review-actions.tsx` pattern (confirmation dialog, `apiFetch`, `router.refresh()`).

## Testing

`apps/api/tests/test_github.py`, mocking `app.github` module functions directly (same style as `test_dispatch.py`/`test_reviews.py` mocking `rpc`):
- Install callback: valid state → row created/upserted; expired/tampered state → 400.
- Repo listing: merges repos across installation(s) for the org; cross-org caller → empty (RLS).
- Merge: org with installation → installation token path; org without → `GITHUB_TOKEN` fallback; neither → existing 409.
- Revert: happy path → new PR URL + `task_events` row; simulated PR → 409; task not `merged` → 404/409.
- Disconnect: uninstall call made, row removed; cross-org disconnect attempt → 404 (RLS hides the row).

## Out of scope (unchanged from story)

- Webhook-driven sync of PR/CI status into Supabase.
- Per-user (vs per-org) GitHub identity/permissions inside the factory.
- Fine-grained repo-level permission editing from within Software Factory (managed on GitHub's own install-configuration screen).
- Runner-side (`apps/runner`) token consumption — deferred to whenever US-1.15 actually builds `provider_claude.py`.
