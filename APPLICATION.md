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
- [Delivery history](#delivery-history) — below: the shipped phases (1–96), condensed to what still matters; full summaries in git history

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

Three gaps from that phase are still open and are recorded here so they are not
rediscovered by hitting them: `platform_run_config.model_routes` is empty, so an
interactive agent created by the wizard resolves a model from nowhere and refuses to
run until one is set on the agent itself (us-78.5 AC3 is not true yet — choosing a
fleet-wide default is a superadmin decision about which model and whose money, which
is why it was left rather than picked); no Buildmill-owned fork of
`xai-org/grok-build` exists, so the provisioner installs upstream's binary under our
own name (us-78.1 AC1 unmet, and Apache-2.0 §6 means the name must change before this
is presented as a product); and the CLI-window button's glow (us-78.11 AC2) has never
been observed, because it needs an interactive agent holding a running run at the
moment someone looks at the Team page.

**Phases 79–90** (41 stories, confirmed 2026-08-13) — the second hardening pass:
production error truth, a testing process that actually runs, an app made fast, and a
release that can retry.

- **Production error truth** (79) answered the first real harvest of the
  self-monitoring inbox — prod BUG-1…BUG-8, promoted 2026-08-11 — in noise-first
  order: silence the mask and the two hang-up floods so real crashes are visible
  (79.7, 79.6, 79.3), make a database outage legible as a 504 in words (79.5), then
  the manager-facing diagnoses (79.2's failed merge naming its credential and its
  cure, 79.4, 79.1). us-79.8 widened the phase past API exceptions to the errors that
  never become exceptions — an agent dying holding its claim, a lease expiring in
  silence — as an **Agent failures** console beside System issues (migration 238;
  `record_agent_failure` wired into `complete_run`, `requeue_expired_claims`,
  `requeue_stale_heartbeats`). Its refinement over the draft is the shape worth
  copying: orgs/projects/issues/runs stay member-scoped, so the platform-admin console
  reads them through two `is_platform_admin()`-gated `security definer` functions
  rather than directly.
- **Tests you will actually run** (80) cut the api suite from ~30 minutes to ~30
  seconds, and the diagnosis is the durable part: `--durations=40` killed the
  hogs theory (the forty slowest summed to ~280s of ~1800s), and a probe that made
  `socket.connect` raise proved the real cause — a fake-backed route test whose read
  is not faked calls `https://test.supabase.co` **for real** and waits on it, then
  turns the failure into the refusal it asserts. The assertion passes either way; it
  just costs seconds. Essential blocks outbound resolution and holds back only
  `*_sql.py`, `needs_db` and `slow`; Full QA (`--full`) runs everything. Nothing was
  deleted. See Testing in CLAUDE.md for the live contract.
- **The reimagined testing process** (81, 82) turned six test types into one
  mechanism, split by who is competent to do the job: **authoring is LLM work**
  (specs land in the target repo via reviewed changesets), **execution is
  deterministic** (a `deploy.py`-style SSH pipeline runs the suites against the
  deployed UAT instance and parses JUnit XML — no LLM in the path). Suites are
  declared per project (migration 239); the pipeline (240) pins the commit, runs under
  `timeout` with `SF_BASE_URL`/`SF_COMMIT_SHA`/`SF_RESULTS_PATH`, and treats **JUnit
  as truth with the exit code merely informative** — `error` is not `failed`, exit 124
  is `timed-out`, and suites sharing a server serialize on a per-server lock. Results
  are member-read-only with no write policies, so a browser cannot forge them. UAT
  deploys trigger suites automatically (status-guarded, exceptions swallowed so the
  deploy's own outcome stands); results map `(suite_id, spec_ref)` onto the release's
  copied cases and reach the sign-off gate (241), which gates only on
  `blocks_signoff=true` and is advisory by default with a per-suite waiver that stamps
  who/when/why. Plan-time automated-case authoring (242) and worker-side pre-submit
  evidence (244) both ride **rendering-side** context, deliberately, so a project-level
  instruction override can never shadow the authoring contract. Phase 82 added prod
  smoke after go-live (fire-and-forget — a smoke verdict can never fail the
  confirmation; a failure points at "Mark rolled back" and **the human decides**, never
  auto-rollback), prose→spec conversion as ordinary chores, a module taxonomy computed
  from the real commit range that *suggests* manual regression cases (243), and
  one-click adoption of untracked specs. Unit tests stay out of the case DB — code,
  recorded as evidence, never tracked rows.
- **Interactive agent stability** (83) hardened Phase 78 against what an audit of the
  real CLI (grok 1.0.0, live handshake) found: the fleet's CLI self-updated and
  ingested Claude/Cursor config from workspace repos by default (83.1 pins the version
  and closes both doors); the CLI-window path had never worked and duplicated the run
  path's session-open at lower maturity (83.2 makes `acp/engine.py` one engine, two
  owners — migration 245); the promised idle close had no caller and a crashed session
  CLI held the agent forever (83.3 — graceful close measured live, and the capability
  is declared as an **empty object**, so the check is presence, not truthiness);
  and escalation's effort setting was silently dropped while the CLI measurably accepts
  it, with truncated answers scoring as success (83.4 — the effort vocabulary was read
  off the live handshake, not guessed, and truncation stops now fail the run with the
  partial text preserved). Fixed straight on `main`, not a story: the capability check
  in `agent_sessions.py` called a nonexistent RPC (`has_capability` →
  `has_org_capability`), 403ing every session open.
- **Manager ergonomics** (84, 85.2, 86.2) — a feature's header row clears the
  unanimous gate in one click (Curate/Plan/Code/Approve all), projection only, no new
  endpoint or migration; a batch dispatch became one ordered request that sorts by
  build order regardless of click order and reports skipped items verbatim rather than
  dropping them silently; and Waiting on you shows the build, not its cargo, when a
  feature owns the run.
- **Agent readiness** (85.1) gave the manager a Prepare workspace action per project
  row (migration 246, `workspace_prep_jobs`): a background job on the agent's runner
  creates the working directory, fetches code, writes agent + MCP config, registers
  granted Tool servers, then **verifies** the environment (shell, git, factory MCP,
  tool handshakes) and reports the resolved run settings — so the first dispatched task
  starts on a known-good workspace instead of discovering a broken one.
- **Routing, simplified** (86.1) replaced the build-mode radio and Concurrency
  checkbox with two switches and one law. "Follow the build order" (default on) keeps
  Epic→Feature→Story ordering; off queues anything in any order — **the switch frees
  the order, never the law**. "Route the feature as one" (default on) makes the feature
  the routing unit: batch plan, one feature-owned code run and PR, and one repo docs
  commit per batch action instead of one per story. `sequential_only`'s dispatch freeze
  was deleted outright; the law replacing it has no checkbox — a project works one item
  at a time, start to merge, and everything queued behind it waits wearing the us-74.5
  hourglass with its reason on every surface. **Dispatch itself is never refused**
  (migration 247). The rewrite surfaced 19 older pool/capabilities/liveness tests whose
  scenes assumed multiple concurrent offerings per project; all 19 were re-scened to
  the law with zero changes to the migration — no genuine defect found.
- **The app gets fast** (87.1–87.7, 87.11, 87.12) fixed an app laggy at 63 work items
  that would not have survived hundreds of projects, and the lesson is that the lag was
  never data volume — it was fixed cost. The shell recomputed the whole Things-to-Do
  dataset on **every** navigation to print one badge, reading `principals` three times
  by the same key across six sequential round trips (87.1's `React.cache()` request
  cache; 87.2's `org_pending_count`, whose per-group counts were run against prod's
  live data for all nine organizations before being wired). Work Items loaded every
  item in the workspace including each one's **full markdown body**, unscoped and
  unbounded, then filtered in the browser (87.3 — `body` and `acceptance_criteria` off
  the list select, one `hub-query.ts` definition shared by the server's first page and
  the browser's Load more, ordered `(updated_at desc, id desc)` so a timestamp tie
  across a page boundary cannot repeat one row and lose another; 87.4 adds CSS
  `content-visibility` rather than windowing, which was the wrong tool for a nested
  collapsible tree). The single largest win was Realtime: **89.5% of all database
  execution time** (28,016s of 31,286s over six weeks) was decoding WAL for 27
  published tables, trimmed to 20 (87.5) — and that audit earned its keep, because
  `clarifications` and `releases` look unsubscribed until you read
  `shell-live-count.tsx`, and dropping them would have silently frozen the sidebar
  badge. Underneath, the API opened a fresh Postgres connection at each of 214 call
  sites with no pool (87.6 — all five `_connect` definitions now lease from one
  bounded `app/pool.py`), and authenticated workers with an `UPDATE` that had run
  940,000 times, feeding that same WAL (87.7 — auth is now a `SELECT`, presence a
  throttled mark at most once per worker per 15s). Then the follow-on, from the
  manager's report after the release — *"it refreshes so fast, there is no transition
  or progress indication"*: making the app fast removed the feedback its slowness used
  to provide. 87.11 recalibrated the existing signal (a 1.1s sweep starting off-screen
  showed nothing at all on a 150ms navigation) into an indeterminate fill with a
  minimum visible duration, plus `loading.tsx` skeletons on the six heaviest routes
  (there were zero) and React `<ViewTransition>`; 87.12 gave live updates their own
  local tint-and-fade, with twenty simultaneous changes standing down rather than
  flashing the whole list.
- **The agent window reads like a terminal** (88.1) fixed the screen where the manager
  watches an agent work. ACP streams message and thought chunks token by token; each
  was stripped and run through the whitespace-collapsing clipper, then rejoined with a
  space — deleting every newline in the agent's markdown and putting spaces around
  every punctuation-only token (`a health check command .`). Chunks now rejoin
  verbatim, and the console is dressed as the terminal it is: fixed dark surface, mono,
  a gutter glyph per event kind, rows on a grid so wrapped text hang-indents past the
  glyph column.
- **The zero-secret workspace** (89.1, 89.2), the manager's direction after the
  FEAT-2.8 token commit: the worker token stops being copied. No token-in-remote-URL,
  token-in-workspace-config, token-in-helper-script, or write-then-delete dances — one
  home (the slot env file), everything else brokered by the supervisor. A git
  credential helper answers fetch/push reading `FACTORY_WORKER_TOKEN` from the process
  env at git time (so the CLI agent's own `git push` authenticates the same way and
  rotation touches no workspace), and a loopback MCP broker injects the header, so
  workspace files carry at most a machine-local key that is worthless off the box.
  Rotation is one file plus one restart — the same zero-secret pattern the LLM gateway
  already proved for model keys (US-10.3), applied to the last secret on the box.
  89.2 added the manager-facing layer: a per-project Environment section (entries plain
  or write-only-secret, optionally agent-scoped) delivered as process env at CLI spawn
  and discoverable over MCP, with scrubber registration and a changeset sweep so no
  delivered secret can ride a commit.
- **Release resilience** (90.1), drafted from release 2026.08.13.1's death: a release
  that failed before anything shipped gets a Retry that re-runs **only the failed leg**
  — a fresh notes prep or a fresh UAT deploy — on the same version and the same pinned
  commit, with completed legs never redone. Immutability is sharpened, not weakened: a
  version names exactly one build forever, so a failed **attempt** retries while a
  **rejected** build never does (supersede stays). The endpoint takes no body at all —
  the retry reads `commit_sha` and `version` off the stored release even when the
  request names a different commit — and every attempt is audited
  (`release_prep_runs.requested_by`, migration 251) and counted, so a third try reads
  as a third try.

**Phases 91–96** (42 stories, closed 2026-08-15 — built and released to production;
the manager tests on live rather than through per-story UAT sittings, so what each
story recorded as *not proven* is listed rather than assumed) — the manager's surfaces
made to read like the job, the app on a phone, the front door, the beta gate, cost
management, and the type-shaped pipeline.

- **The dashboard reads like the job** (91) — "Waiting on you" became **Dispatch**
  (`?tab=waiting` kept for saved links) with **In Progress** above it: only rows with a
  live worker claim, in the factory table's row shape, the Stage column reading the *run*
  rather than `issues.status`; a CLI-window door per running row (91.3), project grouping
  with per-section collapse in `localStorage` (91.4 — folding is a view state, never a
  filter). Work Items' status filter is a checkbox set opening on "all but merged, done"
  (91.5); the test library pages ten rows with page-scoped select-all (91.6); Reports are
  **Bug Reports** under Activity (91.7); the activity feed pages ten (91.8); project cards
  say `Live: <version> · deployed <when>` (91.9); SuperAdmin is four menus (91.10). The
  measurement spine is migration **252** (91.11): `runs.work_seconds` written once at
  terminal state, `agent_effort_daily` maintained by trigger in the closing transaction —
  prod backfill 35 agent-day rows, 181/185 runs timed — feeding Team's three KPI tiles
  (91.12) and a cost column plus stored `issues.cost_usd` (91.14). Notifications name the
  agent and go where the thing is (91.15); the build stamp is `commit/built_at/ref/version`
  written identically by all three workflows and echoed at `/api/v1/health` (91.16); the
  workspace picker reached mobile (91.17); merged-but-unreleased work asks to be released,
  and an in-flight release *becomes* the card (91.18); the page collapsed to one tabless
  **Workdesk** at `/workbench` with a 301 from `/dashboard` (91.19, route chosen by the
  manager over the story's AC4).
- **The app on a phone** (92) — measured at 375px: Things to Do's 555px of tabs became a
  full-width select still riding `?tab=`, In Progress became cards, five preamble blocks
  folded into one strip (92.1); Work Items' eleven controls became three plus a `Filters`
  sheet, Table cut from 856px to 353px (92.2); Releases' nine-column tables became cards
  with a UAT → Production journey line and full-width actions (92.3); test cases and Bug
  Reports became cards leading with their own words, one `detailFor(report)` shared by row
  and card (92.4, 92.5); project cards lead with state, Archive/Delete behind `⋯` (92.6).
- **The front door** (93.1) — `apps/public/index.html` rewritten as one static page (what
  it is, the seven-step journey with three "your gate" steps, the real git remote and
  MCP agents beside traditional clients); exactly 3 same-origin requests, zero console
  errors, audited at 375×812 and 360×780 with zero overflow. `buildmill.dev` had been
  serving it all along — the login the manager saw was `app.buildmill.dev`.
- **The beta gate** (94.1, migration **253**) — a new account logs in and waits at `/gate`;
  enforcement is one choke point, `is_approved_user()` folded into `is_org_member` /
  `is_org_owner` / `has_org_capability` (the three live bodies md5-pinned across prod and
  dev, raising on drift), so RLS and every capability RPC refuse a pending user; a pending
  queue with Approve on `/admin/users`. Proven as the `authenticated` role in rolled-back
  transactions; grandfathering 7 profiles / 0 pending on both projects.
- **Cost management** (95, migration **254**) — `view_costs` seeded true for owner and
  admin only; `/costs` takes the report whole (four dimensions, four windows, tokens,
  cache share, unmeasured-calls badge, subscription off-meter line), rates moved to
  `/settings/llm-providers`, `/settings/spend` redirects (95.1); a daily curve and
  window-over-window via `spend_trend` (previous window `None` rather than a percentage of
  zero; 403 for anyone without `view_costs`) (95.2); three work-shaped dimensions — Type,
  Epic, Work item — with an honest "Not attributable to a work item" bucket (95.3; the
  type query answered on live dev); Project/Agent/Type filters entirely in the URL (95.4).
  Found in passing: the breakdown's `order by 4` had meant "order by cache reads" since
  US-38.1 inserted two columns — fixed, with a regression test.
- **The type shapes the path** (96) — a chore is single-shot (**255**: `dispatch_kind_for`
  answers `code` from draft/ready/failed/needs-fixes; a plan dispatch on a chore refuses
  by name; `chore` instruction kind backfilled everywhere) (96.1); a bug explains itself
  before the fix (**256**: the think-phase is a five-section RCA reusing the plan machinery
  whole — `bug_rca`/`bug_fix` kinds, "NO diffs, NO patches", the repro as the regression
  case, "Approve RCA" on the gate) (96.2); `standalone_plan`/`standalone_code` complete the
  family, resolved by parentage in one shared mapping (**257**) (96.3); the feature holds
  the steering wheel for planning too — a never-planned child refuses individually and
  `dispatch_feature_batch` lifts it through a transaction-local flag (**258**) (96.4); the
  rails, status labels and a drawn **How each type is routed** help section match the work
  (96.5); a failed breakdown leaves the feature at `ready` with the prior error beside the
  retry (**260**) (96.6); a feature is one triage item — one Workbench row, one queue row
  that is also the drag/pause unit, `org_pending_count` on `distinct coalesce(parent_id,
  id)` (**259**) (96.7). Three incident-driven closes: the hand-back speaks one voice per
  transport and echoes `received`/`dropped` (96.8, from run `51cd4fd3`); a manager stop
  is an answer — short-circuits the repair ladder, closes the run as "stopped by the
  manager", writes no `agent_failures` row, returns the issue to `prev_issue_status`, with
  a claim preflight before every boot (96.9, from run `22b807a5`); the stage shapes the
  model (96.10, `claude-opus-4-8` gone from first-party code); and a key never rides the
  trace — `supervisor/redact.py` plus `db.scrub_credential_patterns` at write time
  (96.11).

**What Phases 91–96 recorded as not proven, and what is left to the manager.** In
91: the feature-owned build as one row (91.2 AC3) and the version/release link on a
project card (91.9) were never exercised on dev (no feature build, no release build
there); **paused spans are not subtracted from `work_seconds`** (91.11 AC1 amended —
nothing records a pause interval; closing it needs a pause ledger); the per-agent effort
line was not visible on dev (91.12); the readable-count contrast (91.13) and the status
filter's real pointer path (91.5) were unit-tested, not driven. In 92: Bug Reports cards
were built to measured widths but never seen with data (92.5). In 93: the live deploy,
real social scrapers and a physical phone. In 94: **the gate page names no contact
address** — no mailbox exists yet (94.1 AC2, one line once one does); the approval record
is `approved_by/approved_at` plus the API log, not a platform audit table (AC6
deviation); the fresh-signup → approve → next-load loop was not driven in a browser. In
95: the turn-away as a non-admin sees it, the rendered curve and group-bys with data,
and `test_costs_spend_sql.py` (no `DATABASE_URL`) — code-read and route tests only. In
96: the RCA review against a real bug, a full plan-batch → approve → feature-build cycle,
a live failed-breakdown round-trip, a live stop on an interactive run, and every
click-through the stories name as "the UAT script" (96.1–96.9). Two items are **manager
actions**: (96.10) prod's `agent_presets` Plan/Code sit at effort `high` where the table
says `xhigh`, and every `llm_function_routes` row still points at `grok-4.6` — applying
the table is a live rewrite of production routing and was deliberately left as a
manager click (the SQL is in the story's git history), so 96.10 AC3's ledger proof is
unmet until it lands; and (96.11) the prod sweep scrubbed **119** credential-shaped
`run_trace` rows, **11 of them worker tokens** that had sat readable in the dashboard —
**rotate the worker token(s) on Settings → Workers**.

**What these phases did not prove.** Much of 81–82 is verified by tests and build, not
by a live round trip: no suite has yet run end-to-end against a real server, no machine
verdict has landed on a real release, no cut has computed touched modules against a
real repo, and no agent has authored an automated case or reported test evidence live.
Each rides the next release that exercises it. This is recorded rather than smoothed
over, per the same evidence-over-vocabulary rule Phase 78 established.

**Phases 98–102** (27 stories, closed 2026-08-16 — built and released to production
in two releases; the manager tests on live, so what each story recorded as *not
proven* is listed rather than assumed) — landing many branches at once, moving the
instructions into the repository, collapsing them to one document, rebuilding what a
release hands the manager, and the Costs page.

- **Many branches, one landing** (98) — `merge` becomes a run kind (migration 261),
  dispatched on a chore, which keeps the chore's single-shot shape. A chore carries the
  branch list that is the merge's whole subject (262), validated where it is written and
  frozen into `input_context` with each head sha; the claim authorises reading several
  refs, and `get_workspace(ref=…)` answers a **full tree** per branch, never a delta,
  because a delta of a branch you have not seen is a silently wrong tree. The result
  lands on a factory branch behind a pull request whose body **leads** with the
  per-branch account, and approve merges it with a **merge commit** rather than a squash
  — for the same reason release PRs are never squashed. A branch the agent cannot
  resolve fails the whole merge (`report_merge_failure`), classed `work-fault` so a real
  conflict never counts against runner health.
- **The instructions live in the repo** (99) — one markdown file per instruction kind
  under `.buildmill/`, from a map with exactly one home (`instruction_files.py`, mirrored
  to TypeScript and pinned by a test in both directions, because an unchecked mirror is
  how `run-kinds.ts` came to list seven kinds while the database allowed ten). Build Mill
  owns `AGENTS.md` whole and `CLAUDE.md` is permanently the `@AGENTS.md` pointer;
  hand-written `AGENTS.md` content is destroyed on first publish, an accepted consequence
  stated rather than discovered. Dispatch stops writing to GitHub entirely — the sync
  becomes the manager's own click, driven by migration 135's hash column. `get_work_context`
  carries a **pointer** to the file with the prose as fallback, so the file is authoritative
  and MCP is the door when it is missing.
- **One document, not twenty-two sections** (100) — a project's conventions stop being
  twenty-two catalog rows and become one markdown document, `projects.agent_instructions`
  (263), which **is** `AGENTS.md`'s body; `.buildmill/Guidelines.md` retires hours after
  us-99.3 shipped it. The naming collision the manager reported is fixed in one vocabulary
  across project settings, both template editors and the audit trail. The refresh run
  proposes whole files — the document and any per-task file — accepted or rejected whole.
  And versioning becomes agent work (264): an agent reads the project's rules and proposes
  a version with its reasoning, validated on what is true of a version in *any* scheme
  (single token, tag-safe, free, reasoned) rather than against `YYYY.MM.DD.N` — which
  would re-impose exactly the constraint the story removes. `releases.version` is
  untouched; the proposal is an input to the manager's cut, never the cut.
- **A release explains itself** (101) — the release agent had a version string, four
  fields per work item, commit subject lines and changed paths; it now gets each item's
  body and acceptance criteria, the titles of the cases it is about to inherit (which the
  server copies onto the release seconds after the hand-back, so it had been authoring
  regression cases blind), `touched_modules`, and the migrations in the range. A release
  case gains `section`, `sort` and `critical` (271) and can finally name the work item it
  tests — `issue_id` had been on the row since 031 and rendered by the page for as long,
  but was never inserted, so every agent-authored case was permanently unattributable.
  The hand-back now refuses a check that is a title with nothing behind it and a release
  that leaves an included item unaccounted for, returning **every** failure at once,
  because one rule per re-run is one agent session per rule. Notes become a declaration
  the app renders (272) rather than the HTML page first asked for: the masthead facts do
  not exist when the agent writes (the UAT deploy is fired **by** the hand-back), and a
  sandboxed frame — the only safe one for agent-authored HTML — can never carry the
  verdict buttons that gate sign-off. The page opens with those facts, every one of which
  it already loaded and never showed, including the deploy result, which had rendered only
  inside a card gated on failure; `releases.migrations` (274) supplies the one fact
  nothing had persisted.
- **Costs leads with the numbers** (102) — the page opens on the week, stops explaining
  itself, and puts six numbers on top that obey the same filters as the table beneath
  them, built from one parameter set so the band cannot answer a different slice.

**What Phases 98–102 recorded as not proven, and what is left to the manager.** The
five stories that closed carrying an unbuilt acceptance criterion, kept here because the
index no longer names them: **98.6 AC1** shipped the per-branch account as markdown
leading the PR body, not as the structured table above the diff the story asked for, and
its AC2/AC4/AC6 ride machinery a merge run was never specifically exercised against;
**99.4 AC7** (auditing each publish) is not built; **99.6 AC4** (a project created from a
template publishes its files — it still seeds `worker_instructions` only, and `.buildmill/`
appears on the first manual publish) and **AC5** (the editor previewing which file a
section becomes) are not built; **99.7 AC3/AC5** (accept/decline per instruction, and a
declined offer staying declined) are not built, so a template edit can be *seen* but not
actioned, and AC1 computes the comparison on read, meaning nothing notifies a manager;
**100.1** deliberately leaves the `project_guidelines` **drop migration unwritten** — the
table is still referenced by `guideline_recommendations.section_id` and the legacy accept
path, and it is the only rollback for 263's backfill, so dropping it is a separate later
migration. Beyond those: **no merge run has ever been executed**, and **no release-prep
run has been executed under Phase 101** — the chain from claim-carries-the-instruction to
agent-writes-to-it is proved by tests and by reading, not by having run. Phase 101's and
102's **entire visual layer has never been rendered in a browser**: the checkout they were
built in had no `apps/web/.env.local`, so the dev server could not construct a Supabase
client. Both phases were released to production at the manager's explicit direction
without a UAT sitting. Migrations 271–274 are additive and were applied to both databases
ahead of the deploy, so a rollback is a redeploy that leaves them harmlessly in place.

**Fixed in passing, and worth remembering** (101): `db.release_versions_for_prep` queried
`public.release_preps`, a table that exists nowhere — so us-100.6's version proposal would
have raised `UndefinedTable` the first time an agent used it, and had not, only because no
prompt ever told an agent the parameter existed. Migration 269 had rewritten the `release`
instruction eight days earlier and left it naming `submit_release_run` (not a tool) and
three deploy tools that resolve a `runs` row a release prep does not have, while asserting
the Agent Instructions were in a context that carried nothing; 273 rewrites it and the
claim now delivers the instruction, the document and the generated notes vocabulary.
Commit messages had been reaching the agent as a one-element **list** under a
string-typed key, and a `path_prefix` query over a file list GitHub had already capped at
300 answered as though complete — so "this release ran no migrations" and "they fell off
the end upstream" were the same empty list.

**Phases 103–112** (20 stories, closed 2026-08-17 — built and released to production;
the manager tested on live, so what each story recorded as *not proven* is listed
rather than assumed; Phase 108 and Phases 113–114 stay open) — a release that cannot
get stuck, work that existed nowhere safe, honest outcomes on the Reports hub and the
work item, a Workbench that acts on a draft, and a Team page rebuilt from the row up.

- **A release cannot get stuck** (103) — from the 2026.08.16.3 incident: the runner
  restarted ten minutes into a prep, the `release_prep_runs` row stayed `running` two
  and a half hours after its lease expired, and `releases_one_in_flight_per_project`
  (215) blocked every future cut for the project while `/cancel` took only `queued` and
  `/retry` only `failed`. Release prep was the one claimed job with no reaper — nothing
  read `claim_expires_at`. `db.reap_expired_release_preps` (103.1) fails every expired
  `running` prep through the existing `_fail_release_prep` body (`for update of p skip
  locked`), on the same three triggers as `requeue_expired_claims` — startup, the
  60-second liveness loop, and `list_release_prep_pool` — landing on `failed` rather
  than requeuing, because a prep that died may have burned a paid session and Retry is
  the manager's call. `GET /worker/release-prep/held` (103.2) lets a restarted runner
  re-adopt the preps it holds before its first poll — `release_prep.briefing` extracted
  from `claim` so the re-adopted job still carries the Release instruction; the route is
  declared before `/release-prep/{prep_id}` because FastAPI matches in order — with
  `_live_preps` guarding against supervising twice and no second `release_prep_runs`
  row, since it is the same attempt. `POST /releases/{id}/cancel` (103.3) widens to
  `queued`, `running`, `notes-ready` and `uat-deploy-failed` and `_stop_prep_runs` ends
  the job with the release: queued rows deleted, running rows moved to `cancelled` (the
  status 215 defined and nothing had ever written); `/reject` calls the same helper,
  closing the hole where a zombie agent could write notes onto a rejected release, and
  `release_prep.not_running_error` gives submit and heartbeat a sentence naming that the
  manager stopped it. `StopReleaseButton` (exported `STOPPABLE`) separates Stop — the
  attempt failed — from Reject — the build is bad. `release-liveness.ts` (103.4) gives
  the Workbench release card the reading story runs already had — who holds it, held
  minutes, silent, abandoned — with silence exact rather than approximate, since
  `heartbeat_release_prep` is the only writer of `claim_expires_at` and every beat sets
  it to `now() + 2h`; no `last_heartbeat_at`, no migration. Migration **275** (103.5)
  adds one branch to `issue_dispatch_refusal` (235's choke point) so `dispatch_issue`
  refuses `plan`/`code` on a project with a release in flight and
  `org_issue_dispatch_blocks` hands the identical sentence to the Workbench and the
  issue page — a hard refusal, not a soft hold, because a parked run reads as progress
  and a queue should not drain silently into a rejected build's aftermath. `breakdown`,
  `elaborate`, `draw` and `guidelines` stay open; `merge` is deliberately not frozen.
- **The tree can be trusted** (104) — two pieces of work found existing nowhere safe.
  `main` carried five duplicated migration numbers; the fix for 249 had been written on
  2026-08-13, sat on an unpushed branch, went stale and was deleted on 2026-08-16. 104.1
  recovered it from the object store (`git cat-file` on `1a5ebcb`), cherry-picked rather
  than rewrote it, and brought it current: `249_project_env` → **276**, the later 271
  (`a_release_case_knows_its_section`) → **277**; 014, 015 and 205 are grandfathered and
  `test_migration_numbering.py` fails Essential on any new duplicate while asserting the
  grandfathered pairs still collide. The recovered commit's `migrate.py` fix survives:
  the skip-if-applied guard had been dead for every prefixed ledger row (92 of 258 on
  prod) because Supabase stores names verbatim; `strip_prefix` normalises both sides,
  which is what makes the rename safe even though dev records `249_project_env` and prod
  plain `project_env`. 104.2 commits the untracked Playwright suite in `scripts/testing/`
  as written — 22 specs, `run-all.mjs`, `endpoints.json`, `tools/generate-catalog.py` —
  as a fourth suite, since it needs a running service and Essential blocks network by
  design; the tree was scanned for credential shapes and every hit is a documented fake.
- **The Reports hub can close a report honestly** (105) — the hub offered promote or
  Ignore, and Ignoring a bug that was fixed records the opposite of what happened. Mark
  fixed (105.1) reaches the `fixed` status migration 184 already gave the superadmin
  console, on the detail, the desktop row and the phone card; recurrence needed no code
  because `app_issues_open_fingerprint_key` is partial over `('new','triaged')` so a
  `fixed` row cannot be `ingest_report`'s `on conflict` target — the story adds the
  sentence saying so, widens Reopen to any non-`promoted` closed status, and gives
  `23505` its own words. 105.2 calls `promote_app_issue` (183) with `p_epic_id` omitted
  from the list, one click, no dialog — `PromoteDialog` stays beside it for when the
  epic matters — behind a shared `canPromote`; the RPC's own double-promotion guard
  makes a client guard unnecessary. Both UI only, no migration.
- **The Workbench acts on a draft** (106) — Triage was the one group that navigated.
  `triage-action.ts` (106.1) names what `dispatch_kind_for` (255) would run — Dispatch
  planning, Dispatch RCA, Dispatch build — and a `draft` feature, which cannot be
  dispatched at all, gets `draft-prd` posting `/api/v1/issues/{id}/prd/draft`. Triage
  rows carry `org_issue_dispatch_blocks` so a held draft wears the hourglass, keep a
  secondary **Open draft**, lose their green `emphasis`, and stay out of "Dispatch
  selected"/"Dispatch all". No migration, no API change.
- **Fixed is an outcome, and nothing waits forever** (107) — `mark_issue_fixed`
  (migration **278**, 107.1) moves a bug, chore or story to `done` — the status the
  feature rollup already sets, so every consumer counts it — flips the `app_issues` row
  it was promoted from to `fixed`, and completes the parent feature if this was its last
  open child, mirroring `approve_run` (168), in one transaction; refuses features,
  `merged`/`done`, `queued`/`running` and abandoned items, with `lib/mark-fixed.ts`
  restating the rules client-side and a test that they cannot disagree. 107.2 is run
  `f483ee01`: queued six days, never claimed, and the card read "An agent is reading the
  repository" — `requeue_expired_claims` cannot see a run with no lease. `refreshState`
  splits `working` from `waiting`/`stalled` on `claimed_at`, the stalled card offers
  Cancel it against the existing `/api/v1/runs/{id}/cancel`, `lib/capability-gap.ts` and
  `NoCapableWorker` (`UserX`, deliberately not `Hourglass`) render the eligibility
  `blockedReason` US-35.5 had computed and thrown away, and a `QUEUE_AGING_HOURS = 24`
  banner covers every kind — chosen over auto-cancel, which would turn a visibility bug
  into a data-loss bug. 107.3 gives the four `AGENT_ROLES` one icon each in
  `role-icon.tsx` (`iconForKind` resolves through `roleOfKind`, so a kind cannot
  disagree with its role); the roster draws all four and greys the absent ones, `null`
  `enabled_kinds` lights all four, and `ActionGlyph` wears the capability its run needs
  — except `failed` re-dispatch, which keeps `RotateCcw` rather than guess.
- **The Team page answers at a glance** (109) — 109.1 strips the row to what is
  scanned: the module pill, token count, join date and output figures move to About and
  Output (last 30 days) in the expand panel; the seat stays (two agents may share a
  name); Remove — the one irreversible action beside Suspend — leaves the row for the
  agent's settings page and a person's expand panel via one `RemoveMember`; the duplicate
  runner-console door goes. 109.2 adds Spent, Lines of code and Human equivalent tiles
  summed in the existing `agent_effort_daily` loop, the estimate from
  `lib/human-equivalent.ts` (`HUMAN_LINES_PER_HOUR` 25, `REMOVED_LINE_WEIGHT` 0.5) with
  "rough estimate from lines changed" on its face. That tile read 72,841 hours, and 109.3
  found run `60af1e2a`: 7,999 files, 1,788,138 lines — a vendored tree, 98.3% of the
  workspace's output, from a revoked worker. `compute_diff_metrics` now classifies
  `vendored` first (`VENDORED_DIRS`/`VENDORED_FILES`/`VENDORED_SUFFIXES`, whole path
  segments so `redistribute/` and `buildings.py` still count) and `lines_added`,
  `lines_removed`, `files_changed` count authored files only, while `change_breakdown`
  keeps them marked; `recompute_run_metrics` repairs `runs` and only the touched
  `agent_effort_daily` keys, dry by default.
- **An agent's projects are the ones you checked** (110) — the wizard asked the project
  question twice, `workers.project_id` and `worker_capabilities`, written by two calls
  that never read each other, with helper sentences that contradicted; the code sided
  with the scope, so an agent with two projects checked never claimed the second's runs.
  Migration **279** drops `workers.project_id`, its index and `set_worker_project`, and
  recreates `create_worker` without `p_project`; `_scoped_project` and every branch
  reading it leave `factory_mcp.py`, `worker_run_refusal` is the only claim gate, a shared
  `_default_project` gives the ten no-claim tools the worker's sole grant, the pool
  listings return `project_id`, and the retired `/mcp/<org-shortname>/<project-slug>`
  URL (404 since 216) leaves all ten refusals. Read from prod first: 63 workers, 28
  scoped, only one active (`d7b07e1d`) narrower than its grants; nothing narrows.
- **The Add agent wizard asks in the right order** (111) — roles move to step 1 beside
  the name, Agent Type to step 2 above the placement it constrains (`interactive` is
  `poolOnly`, and two snap-backs existed to undo a contradiction created a page earlier),
  step 3 becomes Projects. Interactive is first and the default *conditionally* — only
  when a selectable pool exists, so Next is never dead on arrival. Both snap-backs stay,
  with the reason recorded: a machine can still be picked and then a pool-only type.
- **The Team roster is a table** (112) — 112.1 makes the roster nine columns on the
  shared `ui/table.tsx`, `Worked` and `Done` right-aligned so agents compare, a person's
  row blank where a person has no answer, `cellsFor(m)` feeding both the table and the
  stacked below-`sm` layout US-68.6 introduced. 112.2 moves Recently done and Performance
  to a **History** tab, renames the list **Activity** (it includes voluntary releases,
  the opposite of done), puts both on `ui/table.tsx`, and tints the expanded row and
  panel `bg-muted/50` because the manager reported the panel "blends with UI".

**What Phases 103–112 recorded as not proven, and what is left to the manager.** Two
things were owed at this close, not merely unproven — both settled the same day.
**Migration 279** was applied to `Software-Factory` on 2026-08-17 (12:20 UTC by the
ledger), and it found the reader us-110.1 had missed: `get_worker_by_token` — the auth
path for every `/worker/*` call and the git remote — still selected `workers.project_id`
without using it, so from 11:49 to 12:57 UTC every worker authentication answered
`UndefinedColumn` (~8,000 failed polls, two crash-inbox reports) until hotfix `cd24e18`
dropped the column from the select and added a guard test that scans every `select …
from public.workers` in the api for it. And **109.3's backfill** — run on 2026-08-17 13:49 UTC through the
`Ops — recompute run metrics` workflow (added that day: the script needs `DATABASE_URL`,
which lives only on the VM, so the workflow SSHes in with the deploy key, dry by default,
`apply` a checkbox): two runs moved (`60af1e2a` +1,788,138 → +2,772 lines, 7,999 → 29
files; `f878c53b` +7,203 → +2,965) and two `agent_effort_daily` rows were repaired; the
succeeded runs' summed `lines_added` went from 1,813,644 to 24,055. AC6/AC7 met. The dominant gap
otherwise is the checkout these were built in: no `apps/web/.env.local`, no
`apps/api/.env`, no `DATABASE_URL`, so almost nothing visual was rendered before it
shipped. **103.1**'s `test_release_prep_reaper_sql.py` (10 tests) was not run, the AC1
concurrency claim rests on `skip locked` untested, and the wiring is verified by reading;
**103.2** restarted no real runner mid-prep, and `list_held_release_preps` is covered
only in Full QA; **103.3**'s button, dialog copy and icon-only variant were never seen
and no live agent had its next heartbeat refused; **103.4**'s derivation is proven but
the card is not — AC1/AC2/AC4/AC5 as visible things, and AC6's cost reasoned not
measured; **103.5**'s `test_release_freeze_sql.py` was not run, `dispatch_issue` raising
the refusal is inferred from 235's structure, and `merge` is a known unfrozen gap.
**104.1** replayed nothing — "numeric order yields the live schema" is argued from the
ledger, and 27 disagreeing ledger rows are untouched by design; **104.2**'s suite was
never run and `endpoints.json` (2026-08-15) lacks `GET /worker/release-prep/held`.
**105.1** AC3 (a recurrence opens a new row) and the `23505` reopen were reasoned from
the index, not reproduced; **105.2** promoted nothing from the list; **106.1** clicked
nothing — AC1–AC5, AC7 reasoned. **107.1**: nothing clicked and `test_embed_ambiguity.py`
could not run — AC1–AC3, AC7 to press, AC3 the only path writing a row the manager did
not select; **107.2**'s cancel path was never executed (the 422 content-type fix is
argued), `requeue_expired_claims` is unchanged, the badge is on two surfaces only, and
run `9f41a332` is still queued and needs a decision; **107.3**: no icon has been seen,
and `text-muted-foreground/30` is unchecked in either theme. **109.1**: no row seen, and
AC5/AC6 are destructive paths never exercised; **109.2**: no tile seen and no total
compared to the rows. **110.1**: the wizard was not rendered and both `*_sql.py` suites
skipped (the four tests proving AC2). **111.1**: nobody has seen it, and the open
question stands — Interactive-by-default is platform-billed and pool-only. **112.1**: no
one has looked at it, AC8's screenshots do not exist, AC1–AC6 unverified, and nine
columns on a narrow laptop is an open question. **112.2** was the exception — AC1, AC3's
empty state and AC5 confirmed on screen in both themes — but AC2/AC4/AC6 were never seen
populated.

**Fixed in passing, and worth remembering.** 103.4 found the Workbench release card's
`if (!mine.length) continue` fired before the in-flight check, so 2026.08.16.3 — carrying
zero items — produced no card at all, and the status filter omitted `notes-ready`,
`deploying` and `uat-deploy-failed`. 105.2 found the detail offering `PromoteDialog` on an
ignored report `promote_app_issue` refuses, and left recorded that the RPC's guard does not
list `fixed`. 107.2's `cancel-stalled-refresh.tsx` sets `Content-Type: application/json`
because `apiFetch` adds the bearer but not the type and FastAPI answers 422 without it.
110.1 found three `test_factory_mcp.py` tests with inline stub rows missing the new
`project_id` key, fixed before commit, and confirmed the 40 database-less failures there
are identical on `HEAD~1`.

**Phase 114** (3 stories, closed 2026-08-17 — built and released to production the same
day; the manager tested on live) — a project template travels as a zip, and a project
edits its files the way a template does.

- **A template travels as a zip** (114.1) — Export and Import on both template pages
  (the superadmin catalog over the admin api, the org's copies over Supabase under
  `manage_project` RLS) and, via us-114.3, on a project. The zip *is* the published
  layout — `AGENTS.md` at the root and `.buildmill/<File>.md` per kind from `KIND_FILES`,
  filled files only, no manifest — built and read in the browser with `fflate` (the
  web app's first zip library; neither page needed the api to touch files it can already
  read and write). Import overwrites the **selected** template and never creates one; a
  file present in the zip is overwritten, an empty file **clears** its section (deleted,
  the editor's own rule — a stored empty string would beat the factory default at
  seeding), an absent file is untouched, a common top-level folder is stripped and
  `__MACOSX`/`.DS_Store` dropped, a zip with no recognisable file is refused, and an
  oversize file (20,000 / 200,000 characters, the admin api's caps) is refused before
  the first write. The confirmation is a **checkbox picker**: one box per group present
  in the zip — `AGENTS.md`, then the seven phase groups the tree draws — each naming its
  files as overwritten / cleared / unchanged; phase groups start checked, `AGENTS.md`
  starts **unchecked** because it is the file most often tuned by hand. The pure part
  (`template-zip.ts`: entries, parse, plan, grouping, filter) is pinned by ten tests.
- **A project edits its files like a template** (114.2) — the project's *Agent
  Instructions* and *Task Instructions* tabs become one **Instructions** tab drawn with
  the templates' own `TemplateFileTree` + `TemplateFileEditor`: *Task processing* (a
  settings row, not a file) above the seventeen-file grouped tree on the left, one
  editor on the right with who last edited the file (or *factory default*), and both
  mark-ready stamps, the publish bar, a History link that follows the active file's
  audit surface, and the refresh dialog above. Writes are unchanged underneath —
  `projects.agent_instructions`, the `worker_instructions` row (upsert on
  `project_id,run_kind`; blank stays blank, there is no fallback to clear to).
  `?tab=guidelines` and `?tab=worker-instructions` still resolve, and the three
  superseded tab files are gone.
- **A project knows its template, and can change it** (114.3) — a banner names the org
  template the project came from (`projects.org_template_id`, written once at creation
  and, until now, surfaced only as a line on Overview) and counts the files that
  **differ** from it, computed at load against the template's document and
  `worker_instruction` sections; a kind the template holds no file for is not compared
  (the seed gave it `default_worker_instruction`) and the editor says so. The editor
  offers **Reset to template** for a differing file (or *Reset to factory default* where
  the template has none), the banner carries the same Export/Import
  (`<slug>-instructions.zip`), and **Change template** lists the org's available
  templates with a plan of overwritten / cleared / unchanged, replaces every file the way
  the seed would (section, else factory default; the document from the template's), and
  re-links `org_template_id`. No migration: drift is computed, and Change template
  reuses the existing columns and rows.

**What Phase 114 recorded as not proven.** 114.1 was verified end-to-end on the org page
(import → stored → export byte-identical; clear, root-file, folder-prefix and unchanged
cases; the checkbox picker writing only checked groups) but the **superadmin page was
never driven** — the dev session's active org is not the platform-admin org, so
`/admin/project-templates` redirects; it renders the same component and its
`applyImport` makes the calls `saveFile` already makes. 114.2 and 114.3 were verified
from the **server-rendered HTML** of the Demo project's tab (the tab strip, the
seventeen-row tree, "6 files differ from template", the Reset / Export / Import / Change
template buttons) and not by clicking: the project page reveals its Suspense boundary
with a React view transition that waits on `requestAnimationFrame`, and the embedded
browser pane was hidden for the whole session, so save, Reset, Change template and the
viewer-role hiding were reasoned from code, not observed. Both went to production at the
manager's direction and were tested on live.

**Phase 113** (1 story, closed 2026-08-17 — built 2026-08-16, released the same night,
proven on live) — **a release prep can hand its notes back** (113.1). Every
`submit_release_notes` had crashed since Phase 101 shipped: us-101.4 put `notes_doc`
into `update_release`'s patch as a raw dict, psycopg 3 has no dumper for `dict`, so
the write raised `cannot adapt type 'dict'` client-side before any SQL was sent — which
is why Postgres logged nothing, and why the agent, reading a server 500 as bad input,
rewrote its notes and retried sixty-one times (138 duplicate `test_cases` rows, $2.21,
no notes) until the CLI wall-clock cap killed it. The value is now `json.dumps`-encoded
like every other jsonb write in `db.py`, `update_release` refuses to hand psycopg a raw
dict, and `test_release_notes_adaptable.py` asserts in-process — no database — that
every value the submit path writes has a dumper, because every existing test of that
path monkeypatched `update_release` and so the failing write had no coverage. Release
2026.08.16.4 was retried and reached `released` with notes at 02:37 UTC (AC5). The two
writes (`attach_release_test_cases`, then `update_release`) are still not one
transaction — that atomicity hole is deliberately left for a later story.

**Retired unbuilt — do not re-propose without a fresh ask** (2026-08-09 sweep): the
`mcp_tool_calls` stall detector (us-69.1) and the `git clean -fd` workspace hygiene fix
(us-69.2 — a shared project workspace still carries untracked sibling-story files between
runs); the Workers-page retirement (us-55.4 — `/workers` stays up); member-page re-homes of
role/suspend and machine-side controls (us-55.2/55.3 — but Reactivate restoring
suspension-revoked tokens DID ship, migration 232); per-slot port ranges (us-57.11 — two
agents on one host can still collide on dev-server ports); work-item cost display (us-46.1);
cache-prefix determinism (us-38.2); hub filter unification, the Agent Runs tab, and the
grouped roster (us-58.2–58.4). **2026-08-17 sweep** (the manager's call after the Phase 114 close — the problems are
real, the stories are not being carried): **us-108.1** the crash-inbox-to-zero pass
(the inbox reached 22 fixed / 1 ignored / 1 new by hand; the eight promoted-but-`draft`
work items it named still sit in `draft`); **us-97.1** a moved GitHub repository
relinking itself or asking (the REST client still does not follow a 301 — git does — so
a rename strands a run at hand-back until `repo_full_name` is fixed by hand, as on
2026-08-15); **us-85.3** classing a broken pool machine as a machine fault rather than
a work fault; **us-87.9** the 117 unindexed foreign keys, 27 unused and 1 duplicate
index the Supabase advisor reports (only migration 252 followed the rule, for its own
tables); **us-87.8** retention — nothing is ever deleted, `api_request_log` was 585k
rows / 106 MB and `runs` 33 MB with diffs in the row, and there is no `pg_cron`
schedule in any migration; **us-87.10** a page-load budget read from `api_request_log`
and `client_perf_events` as a gate.