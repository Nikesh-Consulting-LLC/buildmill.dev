# APPLICATION.md Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Write `APPLICATION.md` at the repo root — one hand-curated file that explains what Software Factory does, covering interfaces and interactions only, readable by both an agent operating the app and an agent developing it.

**Architecture:** A layered reference with seven fixed sections, built one section per task. Every factual claim is read out of source (migrations, routers, MCP tool definitions, Next.js route files) rather than recalled — this doc's whole value is being right about surfaces an agent cannot otherwise discover cheaply.

**Tech Stack:** Markdown only. No code changes, no dependencies, no build step.

**Spec:** [2026-07-19-application-reference-doc-design.md](../specs/2026-07-19-application-reference-doc-design.md)

## Global Constraints

- **Target file:** `APPLICATION.md`, repo root. Nowhere else.
- **In scope:** interfaces and interactions — what a surface exposes, who may call it, what it changes, how objects move through lifecycles.
- **Out of scope, enforced in every task:** internals (algorithms, class structure, file layout inside a component); request/response schemas (OpenAPI and the generated Supabase types own those); the phased roadmap (`README.md` owns it); rationale for architectural choices (`ARCHITECTURE.md` owns it — link, don't restate).
- **No invented facts.** Every status value, endpoint, tool name, and route in this doc must be traceable to a file read during the task that wrote it. If a source is ambiguous, write what the source says and note the ambiguity — do not smooth it over.
- **Tables over prose** wherever a table carries the meaning. Prose only where it cannot.
- **Status vocabulary is quoted exactly** as it appears in the DB constraints, including hyphens: `needs-fixes` and `in-review`, not `needs_fixes` or `in review`.
- **Commit after each task** with the exact message given in that task's final step.

---

### Task 1: Skeleton, mental model, actors & surfaces

**Files:**
- Create: `APPLICATION.md`
- Read for grounding: `ARCHITECTURE.md`, `README.md` (first 40 lines only — the premise, not the roadmap), `apps/api/app/main.py`, `apps/api/app/auth.py`, `apps/api/app/routers/worker.py`, `apps/api/app/routers/runner_socket.py`

**Interfaces:**
- Consumes: nothing (first task)
- Produces: the seven `##` section headings in their final wording, in order. Later tasks fill sections in place and MUST NOT rename or reorder these headings:
  `## What this is`, `## Actors & surfaces`, `## Domain objects`, `## Lifecycles`, `## Interface catalog`, `## End to end: one story`, `## Rules & invariants`

- [ ] **Step 1: Read the grounding sources**

Read `ARCHITECTURE.md` in full. Then read `apps/api/app/auth.py` and skim `apps/api/app/routers/worker.py` and `apps/api/app/routers/runner_socket.py` to confirm how each caller authenticates. You are answering one question: **what are the distinct ways into this system, and what credential does each require?**

- [ ] **Step 2: Create the file with all seven headings and the intro**

Create `APPLICATION.md`. Write the title, a one-line "what this doc is / who it's for" note, a pointer line to `ARCHITECTURE.md` (why the pieces are shaped this way) and `README.md` (what gets built when), then all seven `##` headings from the Interfaces block above, in order, with the remaining six empty.

Fill `## What this is` — target ~30 lines of prose. It must answer, in this order:
1. What the product does in one paragraph — a human manager defines a unit of work, an AI provider turns it into a pull request, the manager reviews and merges.
2. The human-in-the-loop premise: the manager approves; nothing merges or deploys on the agent's own authority.
3. GitHub is the source of truth for code; Supabase is the system of record for everything about the work.
4. The two-tier credential split — coding-agent credentials live runner-side, thinking-task LLM keys live cloud-side in Vault — stated as a fact an agent must respect, with a link to `ARCHITECTURE.md` for why.

- [ ] **Step 3: Write the actors & surfaces table**

Under `## Actors & surfaces`, first a two-sentence framing: these are the distinct ways into the system, and conflating them is the most common agent error. Then this table, with the "Credential" column corrected against what you actually read in Step 1:

```markdown
| Surface | Who calls it | Credential | Entry point |
|---|---|---|---|
| Web UI | Human manager in a browser | Supabase Auth session (JWT), RLS-scoped | `apps/web/src/app/(app)/` |
| Supabase (direct) | Web app, for plain CRUD | Same session, enforced by RLS | Supabase JS SDK |
| FastAPI orchestration | Web app, on the user's behalf | Bearer JWT verified via JWKS | `apps/api/app/routers/` |
| Worker MCP | External agent or IDE worker | `X-Worker-Token` | `apps/api/app/factory_mcp.py` |
| Runner WebSocket | Supervisor runner process | Server-issued runner credential | `apps/api/app/routers/runner_socket.py` |
| Git proxy | Principal pushing through the factory remote | Principal token | `apps/api/app/routers/gitproxy.py` |
```

Then, below the table, one short paragraph per surface — no more than three sentences each — saying what that surface is *for* and what it must never be used for. Do not list individual endpoints or tools here; that is Task 4.

- [ ] **Step 4: Verify the surfaces are complete**

Run:

```bash
ls apps/api/app/routers/ && ls apps/web/src/app
```

Expected: every router file maps to one of the six surfaces in your table, and every top-level web route group is covered by the Web UI row. If a router file fits none of them (for example a surface with its own auth scheme you did not account for), add a row for it. Note in your task report which router files you could not place.

- [ ] **Step 5: Commit**

```bash
git add APPLICATION.md
git commit -m "docs(application): skeleton, mental model, actors and surfaces"
```

---

### Task 2: Domain objects

**Files:**
- Modify: `APPLICATION.md` — fill `## Domain objects`
- Read for grounding: `apps/web/src/lib/supabase/database.types.ts` (the generated table list is the authoritative inventory), plus `infra/supabase/migrations/001_*.sql` for the original core tables

**Interfaces:**
- Consumes: the seven headings from Task 1.
- Produces: the canonical object names and the containment chain used verbatim by Tasks 3, 4, and 5. Whatever names you use here (`project`, `epic`, `issue`, `run`, `deployment`, `principal`, `worker`, `server`) are the names every later section must use.

- [ ] **Step 1: Inventory the tables**

Read the type names in `apps/web/src/lib/supabase/database.types.ts`. This is the full table list. Group them: the ~10 objects an agent must understand to operate the factory, versus supporting tables (joins, audit logs, config) that only matter to a developing agent.

- [ ] **Step 2: Write the core object table**

Under `## Domain objects`, write a table of the core objects only — one row each, aimed at ten to twelve rows:

```markdown
| Object | Is | Belongs to | Key fields an agent cares about |
|---|---|---|---|
| Organization | The tenant boundary; everything is org-scoped | — | id |
| Project | A product plus its linked GitHub repo | Organization | repo, gate config, provider defaults |
```

Continue for the rest. "Key fields" means fields that change behavior or that an agent must read to decide what to do — not a column dump.

- [ ] **Step 3: Write the containment chain**

Below the table, one fenced block showing the chain in one glance, then two or three sentences on the relationships a table cannot express (for example: which objects are org-scoped versus project-scoped, and which can exist without a parent):

```
organization
└── project
    ├── epic
    │   └── issue ──► run ──► pull request ──► deployment
    ├── server
    └── principal (member · worker · agent)
```

Correct this against what you actually found — it is a starting shape, not a verified one.

- [ ] **Step 4: Write the supporting-tables list**

One short list of the remaining tables with a half-line purpose each, under a bold lead-in like `**Supporting tables**`. These exist so a developing agent knows they exist; they do not get their own rows above.

- [ ] **Step 5: Verify every table is accounted for**

Run:

```bash
grep -oE "^      [a-z_]+: \{" apps/web/src/lib/supabase/database.types.ts | sort -u | wc -l
```

Expected: the count of distinct tables. Confirm each one appears either in the core table or the supporting list. Report any you deliberately omitted and why.

- [ ] **Step 6: Commit**

```bash
git add APPLICATION.md
git commit -m "docs(application): domain objects and containment"
```

---

### Task 3: Lifecycles

**Files:**
- Modify: `APPLICATION.md` — fill `## Lifecycles`
- Read for grounding: `infra/supabase/migrations/*.sql` (the `status in (...)` check constraints are authoritative for the vocabulary), `apps/api/app/routers/issues.py`, `apps/api/app/routers/workflow.py`, `apps/api/app/routers/reviews.py`, `apps/api/app/routers/deployments.py`, `apps/api/app/factory_mcp.py` (the `submit_*` and `report_*` tools are the transitions a worker can trigger)

**Interfaces:**
- Consumes: object names from Task 2.
- Produces: the exact status vocabulary quoted by Tasks 5 and 6.

This is the highest-value section for an operating agent and the one most likely to be wrong if inferred. Ground every transition.

- [ ] **Step 1: Extract the status vocabularies**

Run:

```bash
grep -rhoE "status in \([^)]*\)" infra/supabase/migrations | sort -u
```

Expected output includes at least these, which are already confirmed present:

```
status in ('draft', 'queued', 'running', 'needs-fixes', 'in-review', 'merged', 'failed')
status in ('queued', 'running', 'succeeded', 'failed', 'cancelled')
status in ('pending', 'approved', 'rejected')
status in ('draft', 'approved', 'superseded')
```

Then run `grep -rn "status in (" infra/supabase/migrations | sort -u` to learn **which table** each vocabulary belongs to. A vocabulary without its table is useless.

- [ ] **Step 2: Write the issue lifecycle table**

Under `## Lifecycles`, a `###` subsection per lifecycle. Start with the issue — the one that matters most:

```markdown
### Issue status

| From | To | Trigger | Who can | Side effect |
|---|---|---|---|---|
| `draft` | `queued` | Manager dispatches the issue | Manager (web), or a capability-holding principal | A run row is created |
| `queued` | `running` | A worker claims the run | Worker via `claim_work` | Run is held by that worker |
```

Complete every transition in the vocabulary. Fill "Trigger" and "Who can" from the routers and MCP tools you read — do not guess. Where a transition exists in the constraint but you can find no code path that performs it, say so explicitly in the row rather than inventing a trigger.

- [ ] **Step 3: Write the remaining lifecycle tables**

Same five-column shape, one `###` subsection each, for: run outcome, gate result, review decision, deployment state, and any other status vocabulary Step 1 surfaced that belongs to a core object from Task 2. Skip vocabularies belonging to supporting tables unless the transition is something an agent can trigger.

- [ ] **Step 4: Write the terminal-state note**

A short closing paragraph naming which states are terminal, which are recoverable, and what a worker should do when it finds work in a state it did not expect (release it rather than force it forward). Ground the "release it" advice in the actual `release_work` tool in `apps/api/app/factory_mcp.py`.

- [ ] **Step 5: Verify no invented status values**

For each status literal you wrote in backticks, confirm it appears in the Step 1 output. Run:

```bash
grep -oE '`[a-z-]+`' APPLICATION.md | sort -u
```

Expected: every status-looking literal in that list is traceable to a check constraint or to a code path you read. Fix any that are not.

- [ ] **Step 6: Commit**

```bash
git add APPLICATION.md
git commit -m "docs(application): object lifecycles and transitions"
```

---

### Task 4: Interface catalog

**Files:**
- Modify: `APPLICATION.md` — fill `## Interface catalog`
- Read for grounding: every file in `apps/api/app/routers/`, `apps/api/app/factory_mcp.py`, the output of the route listing command in Step 1

**Interfaces:**
- Consumes: surface names from Task 1, object names from Task 2, status values from Task 3.
- Produces: the interface names quoted by Task 6's scenario.

**Constraint specific to this task:** no request or response schemas. Columns are *what it does* and *what it changes*, never *what it takes*. An agent that needs a schema goes to the OpenAPI doc; a doc that duplicates schemas is a doc that lies within a month.

- [ ] **Step 1: Inventory each surface**

```bash
find apps/web/src/app -name "page.tsx" | sort
grep -rnE "@router\.(get|post|patch|put|delete)" apps/api/app/routers/ | wc -l
grep -rnE "^async def [a-z_]+" apps/api/app/factory_mcp.py
```

The third command lists the worker MCP tools. Confirmed present already: `list_available_work`, `list_my_work`, `get_instructions`, `claim_work`, `get_work_context`, `get_repo_tree`, `read_repo_file`, `get_workspace`, `validate_submission`, `get_project_guidelines`, `list_project_documents`, `get_document`, `get_project_learnings`, `submit_learning`, `recommend_guideline_change`, `submit_plan`, `submit_code_work`, `submit_changeset`, `submit_prd`, `submit_stories`, `report_test_results`, `get_run_status`, `get_pr_status`, `report_progress`, `request_clarification`, `get_clarifications`, `release_work`. Verify this list against the command output and correct it — helper functions prefixed with `_` are not tools.

- [ ] **Step 2: Write the web UI route table**

A `### Web UI` subsection. One row per user-facing route, grouped by area (dashboard, projects, issues, review, team, settings, admin). Columns: Route · What the manager does here · What it writes. Roll up dynamic segments — `/projects/[id]/epics/[epicId]` is one row, not one per epic.

- [ ] **Step 3: Write the FastAPI table**

A `### FastAPI orchestration` subsection, grouped by router file with the file named in a bold lead-in. Columns: Endpoint · Caller · What it does · What it changes. Where a router has many similar endpoints, one row per endpoint is still correct — this is the lookup table an agent uses, so completeness beats brevity.

- [ ] **Step 4: Write the worker MCP table**

A `### Worker MCP` subsection — the most important table for an operating agent. Columns: Tool · What it does · What it changes · When to call it. Order the rows by the sequence a worker actually calls them (discover → claim → gather context → submit → report), not alphabetically, and put a one-line note above the table saying so.

- [ ] **Step 5: Write the runner WebSocket and git proxy subsections**

A `### Runner WebSocket` subsection describing the message kinds the control channel carries in each direction, as a table: Direction · Message · Meaning. A `### Git proxy` subsection describing what a principal can and cannot do through the factory remote, and naming the rails that gate it. Read `apps/api/app/routers/gitproxy.py` for both the capability and its limits.

- [ ] **Step 6: Verify coverage**

Run:

```bash
for f in apps/api/app/routers/*.py; do
  n=$(basename "$f" .py)
  case "$n" in __init__) continue;; esac
  grep -q "$n" APPLICATION.md || echo "MISSING: $n"
done
```

Expected: no output. Any `MISSING:` line is a router with no entry in the catalog — add it.

- [ ] **Step 7: Commit**

```bash
git add APPLICATION.md
git commit -m "docs(application): interface catalog for all surfaces"
```

---

### Task 5: End-to-end scenario

**Files:**
- Modify: `APPLICATION.md` — fill `## End to end: one story`
- Read for grounding: `End-To-End-Testing.md`, plus the sections you have already written

**Interfaces:**
- Consumes: interface names from Task 4, status values from Task 3.
- Produces: nothing later tasks depend on.

This section's only job is to prove the layers connect. An agent can hold a correct parts list and still assemble it wrong.

- [ ] **Step 1: Read the existing E2E walkthrough**

Read `End-To-End-Testing.md`. It records a real run of this flow, so it is the best available ground truth for the ordering. Where it disagrees with what you wrote in Tasks 3 and 4, investigate before writing — one of them is stale.

- [ ] **Step 2: Write the numbered walkthrough**

Under `## End to end: one story`, a single numbered walkthrough carrying one issue from definition to deployment. Each numbered step names: the actor, the exact interface used (backticked, matching Task 4's spelling), and the resulting state change (backticked, matching Task 3's vocabulary). Aim for twelve to twenty steps. For example:

```markdown
3. **Worker claims it.** The agent calls `claim_work` with the run id. The run
   is held by that worker; the issue moves `queued` → `running`.
```

Do not introduce any interface or status that does not already appear earlier in the doc. If the scenario needs one, that is a gap in Task 3 or 4 — go back and fill it there first.

- [ ] **Step 3: Write the branch-point note**

After the walkthrough, a short list of the places the happy path forks: gates fail → `needs-fixes` with the failure output attached to the retry; manager rejects → back to the provider with the comment; worker cannot proceed → `request_clarification`; worker abandons → `release_work`. Each one line, each naming the interface.

- [ ] **Step 4: Verify internal consistency**

For every backticked interface name in this section, confirm it appears in the Task 4 catalog. For every backticked status, confirm it appears in the Task 3 tables. Fix mismatches in this section, or fill the gap in the earlier section — never by inventing a new name here.

- [ ] **Step 5: Commit**

```bash
git add APPLICATION.md
git commit -m "docs(application): end-to-end story walkthrough"
```

---

### Task 6: Rules & invariants, cross-links, final review

**Files:**
- Modify: `APPLICATION.md` — fill `## Rules & invariants`
- Modify: `ARCHITECTURE.md` (add a pointer line near the top), `README.md` (add a pointer line near the top), `CLAUDE.md` (add a pointer in the "What this is" section)
- Read for grounding: `CLAUDE.md`, `ARCHITECTURE.md` security section, `infra/supabase/migrations/` RLS policies

**Interfaces:**
- Consumes: everything from Tasks 1-5.
- Produces: the finished document.

- [ ] **Step 1: Write the invariants list**

Under `## Rules & invariants`, a list of things that are always true — where a violation is a bug, not a preference. Each is one or two sentences. Cover at minimum:
- Every table is org-scoped and RLS-protected; cross-org reads are impossible by construction, not by convention.
- Secrets are write-only: LLM keys live in Supabase Vault, server credentials in the private `data` bucket readable only by the service role. Nothing echoes key material back — not an endpoint, not a log, not a signed URL.
- Nothing merges or deploys without an explicit human approval.
- Code lives in GitHub; the factory stores links and mirrored status, never copies.
- Every state change lands in the event log; the audit trail is a query, not a feature.
- A migration that is written but not applied to the live project makes correct code look broken.

Write these as rules addressed to a reader who might otherwise violate them. Ground each against the source — do not copy this list on faith.

- [ ] **Step 2: Add the cross-links**

In `ARCHITECTURE.md`, `README.md`, and `CLAUDE.md`, add one line each pointing to `APPLICATION.md` and saying what it is for — a single sentence, positioned where a reader arriving at that file would see it early. Match each file's existing voice; do not restructure them.

- [ ] **Step 3: Full-document read-through**

Read `APPLICATION.md` start to finish in one pass. Check:
- No section contradicts another (especially status names between Tasks 3, 4, and 5).
- No internals leaked in — if a paragraph explains *how* something works rather than *what it exposes*, cut it.
- No request/response schemas crept into the catalog.
- No "TBD", no empty section, no heading left from Task 1 with nothing under it.

Fix inline.

- [ ] **Step 4: Verify the doc is self-consistent and complete**

```bash
grep -nE "TBD|TODO|FIXME|\?\?\?" APPLICATION.md
grep -cE "^## " APPLICATION.md
```

Expected: the first command prints nothing; the second prints `7`.

- [ ] **Step 5: Commit**

```bash
git add APPLICATION.md ARCHITECTURE.md README.md CLAUDE.md
git commit -m "docs(application): invariants and cross-links from existing docs"
```
