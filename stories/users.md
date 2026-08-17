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
per-story UAT sittings. The same day's sweep retired six unbuilt stories
(us-108.1, 97.1, 85.3, 87.8, 87.9, 87.10) into the do-not-re-propose list. The condensed record —
including what those phases did *not* prove, the manager actions each left open
(96.10's routing table, 96.11's worker-token rotation), the five acceptance criteria
that closed unbuilt (98.6's structured review table, 99.4's publish audit, 99.6's
seed-publishes-files and section preview, 99.7's accept/decline, 100.1's
`project_guidelines` drop migration), the Phase 78 known gaps, and the
retired-unbuilt-do-not-re-propose list — is in
[APPLICATION.md → Delivery history](../APPLICATION.md#delivery-history).

One story is open — the residue of Phase 89's zero-secret workspace:

| Order | Story | Title | Status |
|---|---|---|---|
| 1 | [us-89.3](us-89.3-grok-settings-ride-the-managed-scope.md) | The agent's Grok settings ride the managed scope | New |

**Phase 89 residue — us-89.3** (drafted 2026-08-13) finishes the zero-secret workspace
at the config layer: the factory configures the interactive agent's CLI the way its
vendor documents rather than through ad-hoc files. Everything the factory ENFORCES —
the model block (gateway `base_url`, `env_key = "BUILDMILL_GATEWAY_KEY"`,
`api_backend`), the us-89.1 loopback broker's MCP entry, timeouts and hardening — goes
to the **managed scope** (`$GROK_HOME/.grok/managed_config.toml`, per
docs.x.ai/build/settings), with hard constraints in **requirements.toml** where the CLI
supports them. The user scope stays the agent's own, and the **project scope**
(`.grok/config.toml` inside the workspace repo) is never written by the factory again —
the layer us-83.1 had to harden against injection, and the file that carried a token
into a project repo on 2026-08-13, simply stops being ours. `grok inspect` — the
vendor's own "what configuration loaded" command — becomes the verification, run by
Prepare Workspace step 7 and the session doctor, so a config that silently failed to
load is a named failure rather than a mystery mid-run.

## Standing QA checklists

Not stories — reusable full-surface test scripts, run on demand:

- [Full App Browser QA](us-Full-App-Browser-QA.md)
- [Full Git Router QA](us-Full-Git-Router-QA.md)
