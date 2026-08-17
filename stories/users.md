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

One story is open: Phase 115's move of the interactive agent's tool configuration
into the CLI's own `config.toml` (requested 2026-08-17).

| Order | Story | Title | Status |
|---|---|---|---|
| 1 | [us-115.1](us-115.1-the-agent-reads-its-own-config.md) | The interactive agent's tools come from its own config | Testing |

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
