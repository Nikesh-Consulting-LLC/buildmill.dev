# Software Factory — Application Reference

What the application does, in terms of its interfaces and interactions, for AI agents that
either operate it (as a worker or runner) or develop it (as a contributor). It does not cover
internals, request/response schemas, or the roadmap.

For *why* the pieces are shaped this way, see [ARCHITECTURE.md](ARCHITECTURE.md). For *what*
gets built when, see [README.md](README.md).

**Contents** — this file holds the overview and the rules; the detailed catalogs are one file
each under `docs/application/`. Read only the file your change touches, and update it in the
same commit as the change it describes:

- [What this is](#what-this-is) — below: the human-in-the-loop premise, systems of record, credential tiers
- [Actors & surfaces](docs/application/actors-and-surfaces.md) — every way into the system, who calls it, what credential
- [Domain objects](docs/application/domain-objects.md) — the tables an agent needs to be able to name
- [Lifecycles](docs/application/lifecycles.md) — status values and the transitions between them, per object
- [Interface catalog](docs/application/interface-catalog.md) — every route/tool/message, grouped by surface (Web UI, Supabase, FastAPI, Worker MCP, Runner WebSocket, run_items, docs tree, wireframes, instruction files, Git proxy, LLM Gateway)
- [End to end: one story](docs/application/end-to-end-one-story.md) — the mainstream path, start to finish
- [Rules & invariants](#rules--invariants) — below: always-true properties; a violation is a bug, not a preference
- [Delivery history](#delivery-history) — below: the 62 shipped phases, condensed to what still matters; full summaries in git history

Operating a worker or runner? Read Actors & surfaces, the Worker MCP / Runner WebSocket / Git proxy
subsections of the Interface catalog, and End to end: one story. Changing code? Read Domain objects,
Lifecycles, and Rules & invariants — they're the contract your change must not break.

## What this is

Software Factory is an AI-driven software delivery pipeline with a human manager in the loop.
A manager defines a unit of work — a project tied to a GitHub repo, and within it an issue,
typically a user story, with acceptance criteria — and hands it to an AI provider (a coding
agent, most commonly Claude Code) to turn into a pull request. The agent does the work on its
own: it claims the item, checks out the repo, writes the code, runs the project's configured
gates, and pushes a branch. The manager then opens the review panel — diff and original story
side by side, gate results inline — and approves or rejects.

Approval is the hinge the whole system turns on. A project's branching strategy
(`projects.dev_branch_strategy`) takes one of three values: `story` (the default — one branch and
PR per issue), `work_item` (one branch and PR shared across a feature's stories), or `main`
(commits straight to the default branch, no PR). On the two PR-opening strategies, nothing merges,
and nothing later deploys, on an agent's own authority — an agent can produce a branch and open a
PR, but only an explicit manager approval moves it further. A project the manager has deliberately
configured for `main` is the one standing exception: a worker's code submission there commits
straight to the default branch with no PR and no per-run review gate — but that bypass is itself
a manager's advance configuration choice on the
project, not something a worker or agent can invoke unasked. Deployment has no such exception:
every deployment run, on every project, is a separate action the manager explicitly triggers, never
an automatic continuation of a merge. A rejection returns the item to the provider with the
manager's comment attached as context for the retry, so the next attempt is informed, not
blind. This human-in-the-loop premise holds at every stage the roadmap adds (gates, deploy,
production sign-off): the agent proposes, the manager decides — except where the manager has
already decided, in advance, by configuration.

Two systems of record split the work cleanly. GitHub is the source of truth for code —
branches, PRs, reviews, and CI results live there, and the factory never keeps its own copy of
code, only mirrored links and status pulled in via webhooks. Supabase is the system of record
for everything about the work itself: projects, issues, runs, gate results, reviews, and
per-object append-only logs that make every state change queryable rather than reconstructed. An
agent that wants to know "what happened" reads Supabase; an agent that wants the actual code
reads GitHub.

Credentials are two-tier, and the split is load-bearing, not incidental. *Coding-agent*
credentials — the ones that reach a real repo checkout and invoke a provider CLI — live only on
the operator's own machine (the runner); Supabase and the cloud API never hold them and cannot
use them to touch a repo directly. *Thinking-task* LLM keys — for triage, drafting, summaries —
live cloud-side in Supabase Vault, write-only from the browser and readable only by server-side
code, which sees at most a key's last four characters. An agent operating this system should
never expect to find a coding-agent secret in the cloud, or a thinking-task key on the runner —
see [ARCHITECTURE.md](ARCHITECTURE.md#security--trust-boundaries) for why the boundary sits
there.

**Worker tokens are the deliberate exception, and it is worth stating plainly** (US-14.7).
Unlike an LLM key or a server credential, a worker's `sfw_…` token is **re-readable**: every
`workers` row carries a `vault_secret_id` alongside `token_hash` / `token_last4`, and
`reveal_worker_token(p_worker)` — `security definer` — returns the real value to
`is_own_principal(principal)` **or** an org member with `manage_members`, scoped to the token's
own org. Team → Connect depends on this to render a working `claude mcp add` command with the
operator's actual token. It is a scoped, revocable credential for claiming factory work, not a
key to a third-party account, so recoverability is a usability win rather than a risk — the
remedy for exposure is rotation. Three surfaces used to claim the opposite ("shown once", "only
a hash is stored") while the fourth handed the token out; the claim was the thing that was wrong.
Do not "fix" this by removing the reveal without changing this line first.

## Rules & invariants

Things that are always true about this system — a violation is a bug, not a preference. Addressed
to a reader who might otherwise write code that violates one.

- **A story never moves forward on an agent's word about what it built.** Since US-27.1 the
  completion fan-out reads `run_item_commits` — the record of which commit landed which story —
  and moves only the stories it has a commit for. An agent that claims six and commits four gets
  four moved; the other two return to `run_items.prev_issue_status`. Every layer above this one
  (the `issue_ids` requirement, the explicit `final`, the `allow_partial` refusal) is the agent
  cooperating; this layer does not trust the agent at all, and it is the one that must survive
  any future change.
- **A live-run predicate is an allow-list, never a deny-list.** Every query meaning "this run is
  still going" reads `status in ('queued', 'running')`. A deny-list (`status <> 'succeeded'`)
  would have admitted `cancelled` the day US-27.10 added it, and would admit the next status the
  same way. A test in `tests/test_run_cancel.py` asserts no deny-list exists in `db.py`.
- **A failure reports what the tool said before it reports what anything thinks.** `runs.error`
  on a CLI failure leads with the exit code, the duration and the command's own output; any
  model-authored reading comes after it, labelled, and is discarded when the output contradicts
  it (US-27.12). The incident row and the notification are built from that same string, so the
  ordering holds everywhere without being restated.
- **"Waiting for work" means there is no work.** An agent that cannot claim says which of the
  reasons applies — revoked token, paused, no matching capability grant, or a queue whose items
  are all held (US-27.9). Presence on the control socket is not permission: the handshake
  succeeds once and the socket then survives a revocation indefinitely.

- **Every table enables row-level security. That is not the same claim as "every table's policies
  route through `is_org_member`/`has_org_capability`," and treating them as one is a bug waiting to
  happen.** Every `create table` in `infra/supabase/migrations/` pairs with an
  `enable row level security` call — no exceptions found. Most org-scoped tables the browser reads
  directly do gate their policies on `is_org_member(org)` or a stricter `has_org_capability` check,
  and on the **Supabase (direct)** surface that makes cross-org reads impossible by construction —
  RLS evaluates inside Postgres before a row leaves it, not as an application filter a developer
  could forget to add. But five tables, verified against their own migrations, route access a
  different way — two of them (the policy-free pair below) an agent must never "fix" by adding the
  usual policy:
  - `organizations`, `organization_members`, and `profiles` (`001_initial.sql`) scope by auth
    identity or the org's own id rather than an `org_id` column, because they define the tenant
    boundary rather than sit inside it.
  - `llm_gateway_keys` (`101_llm_gateway_keys.sql`) and `llm_prompt_templates`
    (`057_llm_prompt_templates.sql`) are org-scoped but carry **zero policies, on purpose** — RLS's
    default-deny blocks every client read, write, or list outright, and only `api`'s service-role
    connection touches either table. **Do not add an `is_org_member` policy to either one** — doing
    so would expose scoped LLM gateway keys or prompt overrides to the browser. This is the same
    "never add one" guard the document already gives the private `data` Storage bucket (see the
    secrets bullet below), and it applies here for the same reason.
  - `notifications` (`094_team_notifications.sql`) scopes by recipient identity — a principal sees
    only the rows where `recipient_id` resolves to their own principal row — not by org membership,
    even though the table carries `org_id`.
  - `principals` (`086_unified_principals.sql`) routes through a third helper,
    `shares_org_with_caller`, instead of `is_org_member`/`has_org_capability`: a policy on
    `principals` that called `is_org_member` directly would recurse, since `is_org_member` itself
    joins through `principals`.
  - `role_capabilities` (`087_role_capability_layer.sql`) grants `select` to
    `to authenticated using (true)` — it is the global, org-independent capability matrix, not
    tenant data, so every authenticated user reads the same rows regardless of org.

- **FastAPI orchestration does not get RLS for free — it has to scope itself.** `apps/api/app/db.py`
  connects to Postgres directly with a privileged connection string that bypasses RLS entirely (its
  own header comment: user-facing endpoints go through PostgREST + RLS, but "its claim/callback
  writes go straight to Postgres"). Every *org-scoped* query in that file must filter by `org_id`
  explicitly — `get_worker_by_token` correctly doesn't, since the org isn't known until the token
  resolves it — and a query that omits the filter where one is needed is a real cross-org leak, not
  something Postgres will catch on its own; an agent adding a `db.py` function must add that filter
  itself.

- **Secrets are write-only, and the two stores are genuinely different.** Thinking-task LLM keys
  live in Supabase Vault behind a `security definer` RPC (`set_llm_api_key`, `002_llm_settings.sql`);
  the client can read at most `key_last4`. Server credentials (SSH passwords/keys) live in the
  private `data` Storage bucket, one folder per `<org_id>/servers/<server_id>/`, written and read
  only by `api`'s own service-role connection — that bucket carries **no** `storage.objects`
  policies at all (`019_servers.sql` says so in so many words: never add one), so RLS default-deny
  blocks every client read, list, or download outright. No endpoint, log line, or signed URL echoes
  either kind of key material back; the UI shows at most a fingerprint or last four characters.

- **An agent machine holds exactly one kind of secret.** Provisioning an agent server (Phase 26)
  writes **one worker token per slot** into a 0600 env file and nothing else. Not a model key —
  the brain and every coding CLI reach models through the LLM gateway with short-lived scoped keys
  minted per run. Not a GitHub credential — the supervisor clones through the factory git proxy
  with that same worker token. A compromised agent box therefore costs N revocable tokens and
  nothing more, and that is the property to preserve: never add a third secret to the machine.
  Job logs are readable by any org member, so every line is masked before it is stored, and sudo
  passwords go over stdin — never on a command line, where `ps` on the target would show them.

- **Nothing merges without a human decision, but that decision isn't always per-run.** Unless a
  project's branching strategy is `main`, a code run only reaches `merged` through the manager's
  own `POST /runs/{id}/approve`. A project the manager has configured for `main` is the standing
  exception — a worker's submission there commits straight to the default branch with no PR and no
  per-run review gate — but that bypass is a manager's advance configuration choice, not something
  a worker can invoke on its own. Deployment has no such exception: every deployment run, on every
  project, is triggered by an explicit manager action; nothing deploys automatically as a
  continuation of a merge.

- **Code lives in GitHub; the factory never stores a copy.** Branches, PRs, reviews, and CI results
  are read live or mirrored via webhook; Supabase holds links and status, never file content. An
  agent that wants the actual code reads GitHub — directly, or via `get_repo_tree`/`read_repo_file`/
  `get_workspace` over Worker MCP, which fetch it live — never a cached blob in Supabase.

- **State history is a per-object append-only log, not a status column you have to trust.**
  `issue_events`, `deployment_events`/`deployment_run_events`, `release_record_events`,
  `git_power_grant_events`, `worker_capability_events`, and `content_audit` each record their
  object's timeline, and `approvals` is itself append-only — a gate decision is always a new row,
  never an update to a prior one. An agent that needs "what happened and when" queries these
  tables rather than reconstructing history from the current status alone.

- **A run never moves backward from `succeeded` or `failed`.** Recovery is always a new run row
  produced by re-dispatching the issue, never a status change on the old one — see
  [Run outcome](#run-outcome).

- **A migration that's written but not applied to the live Supabase project makes correct code
  look broken.** Every file in `infra/supabase/migrations/` has to be applied to the live project —
  not just committed — in the same change that adds code depending on it, with
  `apps/web/src/lib/supabase/database.types.ts` regenerated alongside it. An agent chasing a
  "missing column/table" error should check this before assuming the code itself is wrong.

- **The canonical unit of work is an issue, never a task.** Every current surface — API routes
  (`/issues/{id}/...`), MCP tools (`get_work_context`, `submit_code_work`), and the Supabase schema
  (`issues`, `issue_events`, `issue_comments`) — names it `issue`. `ARCHITECTURE.md` still says
  `tasks` in several places, a stale holdover from an earlier draft rather than a second name still
  in use; don't propagate it into new code or docs.

## Delivery history

62 phases, 479 user stories, built 2026-07-14 → 2026-08-09. The per-story files and the
per-phase summary files that used to live in `stories/completed/` were removed in the
2026-08-09 backlog close — git history keeps every one (last present at commit `c14c1b8`).
This is the condensed record: what each area of the app is, stated as the phases that built
it. US-numbers cited throughout this document and the `docs/application/` catalogs refer to
these stories.

- **Core loop & work hierarchy** (Phases 1, 2, 8, 41, 44, 45, 48) — auth, orgs, projects,
  and the issue hierarchy (epic → feature → story/bug/chore); the PRD gate before a feature
  splits into stories; plan runs approved before code runs; wireframes drawn before plans;
  plans deliberately thin; a feature's stories moving together; Work Items as the central
  hub with Outline/Board/Table lenses.
- **Worker connectivity & MCP** (3, 5, 22, 42) — worker tokens and the claim/lease pool; the
  Worker MCP surface (get_work_context, get_workspace/submit_changeset, submit_code_work);
  the repo docs tree (`docs/factory/`) written into every repo; hand-backs that survive an
  agent's formatting.
- **Manager experience** (6, 12, 18, 19, 20, 24, 25, 40, 56, 58, 61, 65, 69, 70, 71) —
  Things to Do as the decision hub; one-click and atomic batch approvals ("Approve & build"
  answers in seconds — docs syncs run in the background); triage flows; TLDRs; dashboard
  density and nesting; epics closing from the outline; release-history paging and deletion.
- **Team, access & governance** (9, 17, 47) — unified principals (humans and agents as one
  roster), the six-role editable capability matrix, admin provisioning, principal router
  tokens; build modes and auto-approve gates; the permission-mode setting removed once it
  could no longer change a run.
- **Runner & agent fleet** (10, 13, 26, 31–32, 34, 35, 53, 55, 57, 60, 61, 66, 68) — the
  operator-side supervisor runner (server-LLM brain, pluggable Claude/Grok/OpenCode modules,
  WebSocket control, self-repair); provisioned agent servers with per-slot workspaces and
  0600 env files; platform-owned pooled machines (a shared machine IS a pool; per-org quota,
  per-slot unix users, org isolation); agent config collapsed to one reasoned-about page
  (role, module, kinds, projects, billing); how-an-agent-runs owned by the platform
  (`platform_run_config`); staged reset and slot self-heal.
- **Money & metering** (33, 37, 38, 52) — the LLM gateway tees every stream into `llm_usage`
  (parsed-vs-unparsed never conflated with zero); budgets are a project's problem, not a
  task's; cache reads priced apart from fresh input; Claude subscription billing as a
  deliberate off-meter path (`runs.billing = 'subscription'`, never a metering gap).
- **Releases & deployment** (7, 21, 23, 50, 51, 63, 70) — the immutable release entity
  (`YYYY.MM.DD.N`, pinned commit, cut → UAT → sign-off → promote); release prep is a system
  job: the agent's whole job is notes (`release_prep_runs`), the UAT deploy fires itself from
  `deploy.py`'s pipeline; deployments can happen outside the factory; the app says which
  build it is.
- **Truth & observability** (11, 14, 15, 27, 36, 39, 54, 59, 62, 72) — a story never moves
  on an agent's word (`run_item_commits`); run traces, cooperative stop, and the reset
  family; incidents when the factory itself breaks; failures lead with what the tool said;
  runs resume rather than lose their place; the analytics/observability hub; changesets that
  smell base64 are refused, and one-click Approve admits a merge conflict.
- **The app reports on itself** (16, 43, 67) — deployed apps self-report bugs into the
  `app_issues` inbox (per-deployment key, fingerprint dedup) for triage into real issues;
  projects write their own guidelines; superadmin-authored project templates a new project
  silently inherits (guidelines, worker instructions, the three project prompts).

**Since the close** (Phases 73–75, 9 stories, confirmed 2026-08-10) — the Team Connect tab
simplified; a batch of manager ergonomics (stories under a feature always sort by number,
"Pass all" on release test cases, test-case rows collapsed to title + verdict, new projects
defaulting to build-by-feature with concurrency on, dependency holds shown as an hourglass on
"Waiting on you"), capped by Help becoming a topic handbook rather than one page; and a
baseline that tells the truth — the API suite's 7 standing failures and lint's 2 standing
errors were all diagnosed as stale tests, not live defects, and fixed without weakening an
assertion (1944 passed / 0 failed). Testing stays local by decision; there is still no CI gate.

**Phases 76–78** (22 stories, confirmed 2026-08-11) — three things. **Errors users
hit reach the superadmin** (76): self-reporting only ever saw *unhandled* HTTP
exceptions, so a manager's "GitHub merge failed" and every WebSocket crash were
invisible in System issues; found by hitting it, not by audit. Alongside it, GitHub
connect binds to the workspace you are actually in — `connect-url` had been taking
an arbitrary membership with `limit 1` and no `order`, which recorded installations
against the wrong org and produced a page claiming to be connected and unconnected
at once. **Four roles, not ten kinds** (77): what an agent does is Planning,
Programming, Testing or Deployment; the ten run kinds stay the dispatcher's
vocabulary and the manager stops having to speak it, with agent type shown as radios
over the CLIs the factory actually creates.

**The Buildmill Interactive Agent** (78) is the substantial one: a third agent type
that holds a **persistent ACP session** ([Zed's Agent Client
Protocol](https://agentclientprotocol.com), JSON-RPC over the child's stdin/stdout)
instead of running a one-shot command line — the vehicle is a fork of
`xai-org/grok-build`, on platform pools only, on a platform-managed model.
`run_session` is the first primitive in the runner that keeps stdin open, and
`supervisor/acp/` the first code here that speaks a protocol to a subprocess; file
access is confined to the session's roots because the box also holds the slot's
worker token. It reuses rather than duplicates: MCP arrives as a `session/new`
parameter (a real improvement on the config-file-and-hope the Grok module still
uses), narration flows through the existing `run.trace` to the existing realtime,
resume rides the us-59 rails via `session/load`, and metering needs nothing new
because the gateway tee already reads whatever crosses it. On top sits the manager's
**console** — attach to a live run, watch typed ACP events, and type back into the
session; the first path in this system from a person to a running agent, reachable
from the agent's own roster row whether or not it is working.

What that phase actually taught, all of it from a real machine and none of it
catchable by a test suite: xAI's installer symlinks `/usr/local/bin/grok`
unconditionally and silently replaced a working Grok Build binary with a different
program; `GROK_HOME` persists between runs, so a config file written for one run
outlives it and decides the next; the CLI sends a model config table's *entry name*
as the model id, not the `model` field inside it; and a success test that trusted the
spec's stop-reason vocabulary threw away a run that had written a complete PRD.
Evidence over vocabulary, the rule us-27.12 already applied to failures, now applies
to successes too. Metering was proven on the first completed call — `grok-4.5`,
`parsed=true`, cost attributed to the run.

The phase closed with **a session that has no work item** (us-78.10): a manager
opens a conversation on an agent against a project with nothing dispatched, in the
same console, and closes it to release the agent. `agent_sessions` is deliberately
not a row in `runs` — a run has a claim, a lease, an item and a review gate, and
putting these there would make every dispatch query and attempt counter learn to
ignore a shape they were never written for. A unique partial index allows one live
session per agent, and the work loop asks whether a session is held **before
polling the pool**, so two conversations can never edit one checkout. Two of its
own criteria are unmet and recorded rather than implied: a session's work is not
yet promotable through the submit path, and `llm_usage.session_id` has a column but
no writer, because the gateway keys usage on `run_id`.

**Retired unbuilt — do not re-propose without a fresh ask** (2026-08-09 sweep): the
`mcp_tool_calls` stall detector (us-69.1) and the `git clean -fd` workspace hygiene fix
(us-69.2 — a shared project workspace still carries untracked sibling-story files between
runs); the Workers-page retirement (us-55.4 — `/workers` stays up); member-page re-homes of
role/suspend and machine-side controls (us-55.2/55.3 — but Reactivate restoring
suspension-revoked tokens DID ship, migration 232); per-slot port ranges (us-57.11 — two
agents on one host can still collide on dev-server ports); work-item cost display (us-46.1);
cache-prefix determinism (us-38.2); hub filter unification, the Agent Runs tab, and the
grouped roster (us-58.2–58.4).
