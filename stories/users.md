# Software Factory — User Stories Index

Open work is one file per story (`us-N.M-slug.md`) in this folder; this file is the index.
New stories take the next free `N.M` (numbers are stable, never reused) and get slotted into
the build sequence where they'll be worked.

Statuses: `New` (written) → `Testing` (built, awaiting the manager's UAT) → `Completed`.
Only the manager moves a story past `Testing`. When a phase closes, its story files are
deleted and the essence lands in [APPLICATION.md → Delivery history](../APPLICATION.md#delivery-history);
git history keeps the full text.

## Open work

The 2026-08-09 backlog close confirmed everything built to that point (62 phases,
479 stories); Phases 73–75 followed on 2026-08-10, Phases 76–78 (22 stories) were
confirmed and collapsed on 2026-08-11, Phases 79–90 (41 stories) on 2026-08-13, and
Phases 91–96 (42 stories) were closed on 2026-08-15, Phases 98–102 (27 stories) on
2026-08-16, and **Phases 103–114 (24 stories) on 2026-08-17** — all built and
released to production, with the manager testing on live rather than through
per-story UAT sittings. The same day's sweep retired seven unbuilt stories
(us-108.1, 97.1, 85.3, 87.8, 87.9, 87.10, 89.3) into the do-not-re-propose list. The condensed record —
including what those phases did *not* prove, the manager actions each left open
(96.10's routing table, 96.11's worker-token rotation), the five acceptance criteria
that closed unbuilt (98.6's structured review table, 99.4's publish audit, 99.6's
seed-publishes-files and section preview, 99.7's accept/decline, 100.1's
`project_guidelines` drop migration), the Phase 78 known gaps, and the
retired-unbuilt-do-not-re-propose list — is in
[APPLICATION.md → Delivery history](../APPLICATION.md#delivery-history).

Nine stories are open: Phase 115's move of the interactive agent's tool
configuration into the CLI's own `config.toml` (built 2026-08-17, released, UAT
outstanding), and Phase 116's eight on agent stability — why an agent that has
a model is refused a session, why every session has died on the machine since
2026-08-14, why Team and the machine page disagree about the same agent, and
why "start" never quite starts (requested 2026-08-17, two investigations the
same day). Build order is the hotfix first (us-116.3), then the model side
(116.1, 116.2, 116.7), then status and start (116.4, 116.5, 116.6), then the
fleet alarm (116.8).

| Order | Story | Title | Status |
|---|---|---|---|
| 1 | [us-115.1](us-115.1-the-agent-reads-its-own-config.md) | The interactive agent's tools come from its own config | Testing |
| 2 | [us-116.3](us-116.3-a-session-opens-through-the-runs-own-door.md) | A session opens through the run's own door | Testing |
| 3 | [us-116.1](us-116.1-a-session-picks-a-model-the-agent-has.md) | A session picks a model the agent actually has | Testing |
| 4 | [us-116.2](us-116.2-an-agent-shows-what-it-is-missing.md) | An agent shows what it is missing | New |
| 5 | [us-116.7](us-116.7-the-orgs-default-model-counts.md) | The org's default model counts | New |
| 6 | [us-116.4](us-116.4-team-and-the-machine-page-say-the-same-thing.md) | Team and the machine page say the same thing | New |
| 7 | [us-116.5](us-116.5-start-means-start.md) | Start means start | New |
| 8 | [us-116.6](us-116.6-a-new-agent-starts-ready.md) | A new agent starts ready | New |
| 9 | [us-116.8](us-116.8-the-fleet-says-when-it-goes-dark.md) | The fleet says when it goes dark, and says a standing fault once | New |

**Phase 116 — An agent says what it is missing, and starts when told**
(requested 2026-08-17; the manager's direction was *reliability over
complexity* — collapse the ways an agent can be "sort of on", do not add
more). Two investigations the same day found the same shape from two ends.

*The model side.* A CLI session resolves its model from `db.session_model`,
two lines that read `model_overrides.code` and stop — skipping
`run_settings.resolve`, the one server-side resolver whose own docstring warns
that a second implementation of the precedence rules "would disagree". It also
hard-codes the `code` kind, so an **Architect** with all six of its roles
pinned to `grok-4.5` is refused a conversation for lacking a model for work it
is configured never to do — and the settings page renders a model picker only
for roles the agent claims, so the Code dropdown the refusal names is not on
the page. Of six active interactive agents on prod, only Programmer passes
that gate — and Programmer's session then dies on the machine anyway:
`session_host._open` still passes a `token` name that US-89.1 removed
(`NameError` on every session since 2026-08-14), and us-115.1 moved the run's
tool config into `config.toml` without touching the session path, so run and
session open the CLI two different ways. **us-116.3** (first, a hotfix) makes
run and session share one open routine and ships it to Pod-001. **us-116.1**
makes a session resolve through the same resolver a run uses, for a kind the
agent actually claims, and makes the remaining refusal name the roles it tried
and both places a model can come from. **us-116.2** puts the answer on the
Team page *before* the click: the roster's State column gains `no-model` and
`no-roles` above the queue-state reasons (today an agent with no roles, no
model and no grants renders identically to a working one — it says `Idle`),
the Start session button is disabled with its reason instead of failing after
the click, and the Model per role block shows what "Inherit the org default"
actually resolves to — on prod that default is "Balanced" with `model: null`,
so blank currently inherits nothing. **us-116.7** gives the resolver a floor:
the org's default LLM provider's `default_model` — chosen by the manager on
Settings → LLM providers and already what the gateway falls back to — so
"no model" is only ever true when nothing was chosen anywhere, runs are
refused at claim instead of on the machine, and the Phase 78 gap ("a
wizard-created agent resolves a model from nowhere") closes without a
platform-wide model.

*The status and start side.* Team says `online` (a live socket) while the
machine page says `Paused` (the wizard's landing state) or "N enabled agents
are not running" (a five-minute-old SSH probe) — different questions,
overlapping words — and presence itself has no expiry: `disconnected_at` is
written only on graceful close or reconnect, `last_seen_at` is heartbeated
every 30 s and read by nothing, the reaper migration 099 promised was never
written, so a hard-killed API leaves every agent "online" for good.
**us-116.4** defines one status (`offline · revoked · stopped · no-roles ·
no-model · no-grants · queue-held · working · ready`, presence = a heartbeat
inside 90 s, one sweep, one view) and makes the roster, the runner page, the
machine page and the superadmin card render that one word. "Start" is three
buttons none of which starts: the roster's ▶/⏸ on an agent row is membership
Suspend/Reactivate, which revokes the token (429 `agent-token` incidents in
14 days; it killed the Sandy fleet on 2026-08-09); Enable flips a flag and
never touches a dead service; Restart is host-authorized so a pool tenant
gets a 404 the runner page swallows in `catch {}`. **us-116.5** gives the
agent one `start` (enable, and restart the service if it is not live) and one
`stop`, authorized on the slot's org, on the roster and every page, with
errors shown, and takes the membership control off agent rows. **us-116.6**
makes the wizard land an agent Ready — placement carries `enabled`, the
config PATCH stops naming `run_routes` (which 403s every non-platform-admin
org today), the Done step shows the roster's status. **us-116.8** adds the two
alarms that were missing on 2026-08-17 when migration 279 took all six agents
down for 68 minutes and 8,023 crash reports told nobody: fleet-dark once,
after two minutes; standing faults once per episode instead of once an hour.
Deliberately not in the phase: zero-downtime deploys, a per-agent readiness
panel, a platform-wide default model, a second presence channel.

**Phase 115 — The interactive agent's tools come from its own config**
(requested 2026-08-17). The interactive agent gets the factory's MCP server as a
`session/new` parameter (us-78.4), and because nothing tells the CLI the session
is headless it resolves `McpInitStrategy::Progressive` — the first LLM turn
starts while the server is still handshaking. Measured in `run_trace`: every
interactive run's first `search_tool` comes back empty 7–23 s after the
handshake; twelve runs on 2026-08-12/13 retried and recovered at 24–90 s, while
all six since 2026-08-15 instead read `.factory-mcp.json` out of the checkout and
drove the factory with 15–44 raw `curl` calls apiece, never touching a
`factory__*` tool. **us-115.1** moves the servers into `$GROK_HOME/config.toml`
as `[mcp_servers.*]` — the file the factory already rewrites every run — with no
credential in the text (`bearer_token_env_var`, value via the child env, both the
broker and the MCP mount taught to accept the bearer form), spawns the CLI with
`MCP_INIT_STRATEGY=blocking`, hands `session/new` an empty list, keeps
`.factory-mcp.json` out of the workspace, and proves the server answers
`tools/list` before a model is paid to discover it. Revives us-89.3, retired
unbuilt the same day, re-scoped to what CLI 1.0.0 actually reads.

## Standing QA checklists

Not stories — reusable full-surface test scripts, run on demand:

- [Full App Browser QA](us-Full-App-Browser-QA.md)
- [Full Git Router QA](us-Full-Git-Router-QA.md)
