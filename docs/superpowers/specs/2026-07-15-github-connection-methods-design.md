# GitHub Connection Methods — Design

**Date:** 2026-07-15
**Status:** Approved design, pre-implementation
**Scope:** Alternative ways to connect the factory to GitHub — a fine-grained PAT pasted into settings (us-3.15) — alongside the existing GitHub App installation (us-1.19). An OAuth device-flow connect for headless/self-hosted setups was designed here too, but its story was dropped on 2026-07-17; the section below stays for the record. This is strictly the **factory → GitHub** link. How apps and workers authenticate **to the factory** (worker tokens, MCP URLs, the us-3.8 git remote) does not change.

## Problem

The factory reaches GitHub exactly one way: the org installs the Software Factory GitHub App (us-1.19), and `api` mints short-lived installation tokens from the App's private key. That single method has three friction points:

1. **Self-hosted setup**: every deployment must register its own GitHub App (app id, private key, callback URL pinned to that deployment's domain) before anything works.
2. **Restricted orgs**: some GitHub orgs disallow installing third-party Apps, but members can issue fine-grained PATs scoped to the repos they need.
3. **Headless connect**: the App install flow requires a browser redirect back to the deployment — awkward when standing up a factory from a terminal.

The reason the factory→GitHub link matters this much is the Phase-3 architecture: **all apps (Claude Code, Codex, VS Code, the runner) use factory-based git and MCP, never GitHub directly**. The upstream link is the one place GitHub credentials live, so it must be connectable everywhere the factory runs.

## Decisions made during brainstorming

| Question | Decision |
|---|---|
| Which alternatives | **Fine-grained PAT (us-3.15)**; device flow designed but later dropped; OAuth web-redirect flow not needed (App install already covers browser connect) |
| Workers cloning GitHub directly | **Dropped** — contradicts the everything-through-the-factory goal; the us-3.8 proxy stays the only git door for workers |
| Data model | One org-scoped **`github_connections`** table with a `method` column (`app` / `pat` / `oauth_user`); existing `github_installations` rows migrate in |
| PAT storage | **Write-only, Vault via `security definer` RPC** — the LLM-key pattern (migration 002); client sees last-4 + expiry only |
| Repo discovery for PATs | **Explicit repo entry, validated live** against GitHub before saving — a PAT can't enumerate its grants the way an installation can |
| Token resolution | One resolver ("token for this org/repo"): a PAT that explicitly lists the requested repo wins first (an explicit, known grant — the App's actual repo coverage can't be checked cheaply), then App installation, then any other PAT, then `GITHUB_TOKEN` env last-resort fallback |
| Device-flow client id | **Shared public client id** (the `gh` CLI model) — device flow needs no client secret and no callback URL, so one upstream registration serves every self-hosted deployment |

## Architecture

```
[ apps: Claude Code / Codex / VS Code / runner ]
        │  worker token → /git/* proxy + /mcp        (unchanged, us-3.1/3.8/3.3)
        ▼
    [ api — FastAPI ]
        │  resolve_github_token(org, repo)           (new seam, us-3.15)
        │    1. pat (repo listed) → Vault            (explicit grant wins)
        │    2. app  → mint installation token       (existing)
        │    3. pat (any)  → read PAT from Vault     (new)
        │    4. env  → settings.github_token         (existing fallback)
        ▼
    [ GitHub — source of truth for code ]
```

Every GitHub call in `apps/api/app/github.py` already takes a bare `token` argument, so the only structural change is the resolver; callers (git proxy, issue sync, merge/revert, deploy fetch, repo reads) are untouched beyond swapping in the resolver.

## Data model (us-3.15)

### New table: `github_connections`

Org-scoped, RLS via `public.is_org_member(org)`, like every table.

| Column | Notes |
|---|---|
| `id` | uuid pk |
| `org_id` | RLS scope |
| `method` | `app` \| `pat` (`oauth_user` reserved for a device-flow method if ever revived) |
| `account_login` | GitHub account the connection reaches |
| `account_type` | user / org |
| `installation_id` | nullable — `app` rows only |
| `pat_last4` | nullable — `pat` rows only; display value |
| `pat_expires_at` | nullable — fine-grained PATs expire (max 1 year); UI warns as it approaches |
| `repos` | jsonb array of `full_name`s — `pat` rows only, entered explicitly and validated live |
| `connected_by`, timestamps | as `github_installations` today |

Existing `github_installations` rows migrate in as `method='app'`; the old table is dropped in the same migration. The PAT secret itself never touches this table — it goes to Vault via a `security definer` RPC (`connect_github_pat`), readable only by `api` (service role), exactly like `set_llm_api_key`.

## Behavior differences designed in, not papered over

- **Repo discovery**: `app` connections keep listing via `/installation/repositories`. `pat` connections list the explicitly entered repos; each entry is validated with `GET /repos/{owner}/{repo}` using the PAT before saving.
- **Attribution**: pushes, merges, and PRs through a PAT show as the token's user, not the App. Surfaced in the settings UI copy, not hidden.
- **Expiry**: `app` tokens are minted fresh per request; PATs are long-lived and expire — the connection stores `expires_at`, the settings page shows it and warns when close. Rotation is manual re-paste (no auto-rotation).
- **Scoping**: installation tokens can be minted per-repo; a PAT is what it is. The resolver never narrows a PAT — the user narrows it at creation on GitHub. A PAT that lists the requested repo is checked first, ahead of the App: that grant is explicit and known, while the App's actual repo coverage can't be checked cheaply at resolve time.

## Device flow (dropped)

`POST /api/v1/github/device/start` asks GitHub for a device code using a public client id (config, with a shared upstream-registered default); the operator enters the short code at github.com/login/device from any browser on any machine — no callback URL, so it works for a factory on a headless box. `api` polls for the grant and stores the result as an `oauth_user` connection, token in Vault, refresh handled server-side. The resolver treats `oauth_user` like `pat`. The story was dropped on 2026-07-17 without being built; this section stays for the record, and the connection model still accommodates an `oauth_user` method if it's ever revived.

## Out of scope

- Any change to app/worker → factory auth (worker tokens, MCP URLs, us-3.8 push policy).
- Workers talking to GitHub directly.
- Human dev flows (e.g. VS Code pushing arbitrary branches) through the factory git remote — the us-3.8 push policy stays worker-run-scoped; a separate story if wanted.
- Auto-rotation or refresh of PATs.
- Git LFS, SSH transport (unchanged from us-3.8's exclusions).

## Testing

pytest with GitHub mocked: PAT connect (validate token + repos), resolver preference order (listed-repo PAT → app → any PAT → env), git proxy upstream auth over a PAT connection, expiry surfacing, cross-org isolation on `github_connections`, and the installations→connections migration.

## Stories

- [us-3.15 — Fine-grained PAT as a GitHub connection method](../../../stories/completed/us-3.15-github-pat-connection.md)
