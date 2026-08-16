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

**Retired unbuilt — do not re-propose without a fresh ask** (2026-08-09 sweep): the
`mcp_tool_calls` stall detector (us-69.1) and the `git clean -fd` workspace hygiene fix
(us-69.2 — a shared project workspace still carries untracked sibling-story files between
runs); the Workers-page retirement (us-55.4 — `/workers` stays up); member-page re-homes of
role/suspend and machine-side controls (us-55.2/55.3 — but Reactivate restoring
suspension-revoked tokens DID ship, migration 232); per-slot port ranges (us-57.11 — two
agents on one host can still collide on dev-server ports); work-item cost display (us-46.1);
cache-prefix determinism (us-38.2); hub filter unification, the Agent Runs tab, and the
grouped roster (us-58.2–58.4).
