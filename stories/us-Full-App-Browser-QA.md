# Full App Browser QA

**Status:** Standing
**Type:** Living QA checklist — not tied to any phase

## Story

As the manager, features are verified **on the real app** — https://app.buildmill.dev — against a real project named **Demo**, not just in unit tests. This is a **living QA checklist** for the whole app, and it has two halves:

- **Part 1 — Browser QA.** Claude drives the app through the integrated browser tools, records PASS/FAIL with evidence, and logs defects. Covers the project setup & release surfaces.
- **Part 2 — Agent end-to-end.** The full delivery pipeline run for real: a manager in the browser and a **headless Claude CLI agent** as the worker, driving one work item from creation to a merged PR. This is the only check that exercises the factory as a system — the app, the API, the MCP surface and a real agent at once.

Both are re-run whenever meaningful changes ship. Part 2 was run twice on 2026-07-20 and is the source of Phases 11, 12 and 13; the procedure below is what those runs used.

**Test the built thing, not the diff.** Verifying Phase 14 locally caught two defects that had already been committed, reviewed and declared done — a flag dropped by a projection one file away from the code that reads it, and an ordering that was only ambiguous when two rows shared a clock tick. Neither is visible in a diff, and both survived a passing type-check, a passing lint, a passing build and 756 passing tests. A story is not finished when it compiles; it is finished when someone has watched it work.

## Test environment & method

- **Target:** https://app.buildmill.dev (production web app) and `https://api.buildmill.dev` (API + MCP), project **Demo** (already created).
- **Executor:** Claude drives the manager side through the integrated browser tools once the features are released and confirmed live, and drives the agent side through the Claude CLI in a shell.
- **Auth:** the manager signs in / provides an authenticated session — **Claude does not enter credentials** (login is the user's to perform). Claude picks up from an authenticated Demo project. The worker token is an application credential the user issues for the run.
- **Release check first.** Part 2 tests what is deployed, not what is on `main`. Before starting, confirm the fixes under test have actually reached `prod` — a passing local build proves nothing about app.buildmill.dev.
- **Recording:** each check gets a PASS / FAIL with a screenshot or the observed evidence; failures capture the exact step, expected vs. actual, and any console/network error.
- **Drive controls by element reference, never by screenshot coordinate.** Read the page's accessibility tree and click the `ref`. A coordinate derived from a scaled screenshot can land a few pixels off and hit the container instead of the control — it fires an event, changes nothing, and looks exactly like a broken button. That misreading produced a defect report and a story before it was caught ([us-14.2](completed/phase-14-legibility-truthfulness.md)). Coordinates are for canvases and images only.
- **Before filing a UI defect, instrument it.** Attach a capture-phase listener and confirm which element actually received the event, and check the handler is attached (`Object.keys(el).find(k => k.startsWith("__reactProps"))`). Repeating an observation through the same instrument raises confidence without adding evidence.

## Part 1 — Browser QA test plan

### A. Project setup & readiness (us-7.6, us-7.7, us-7.8, us-7.4, us-7.5)
- [ ] **Tabs (us-7.6):** Demo's tabs read, in order, Overview · Guidelines · Worker Instructions · Deployments · Documents · Learnings · GitHub. The GitHub tab shows the repo connection and open PRs, with **no** Enable Sync / Pull Issues control.
- [ ] **Overview readiness (us-7.7):** a "Project setup" panel shows six checks with Done/Not-done and working deep links; a fresh Demo reads mostly Not-done; nothing is blocked by incompleteness (advisory only).
- [ ] **Project Summary + brainstorm (us-7.8):** write a Demo summary; the Guidelines tab has **no** Brainstorm button; Brainstorm with AI runs from the Summary card; it asks questions and drafts guideline sections, worker-instruction blocks, and build config; each lands as an "AI-suggested · not saved" item with Accept/Discard; accept some, discard one; accepted content appears in the right tabs.
- [ ] **Mark-ready + edited-since (us-7.4/us-7.5):** mark Guidelines ready and Worker Instructions ready; badges show who/when; edit a section and confirm the "edited since marked ready" nudge appears; re-mark clears it. Confirm the seeded **Versioning & Release** guideline section and the **Versioning & Release** worker-instruction card exist.

### B. Repository, branches & environments (us-7.3, us-7.2)
- [ ] **Release branches (us-7.3):** pick a UAT branch and a Production branch from the repo's branch dropdown; use **New branch** to create one from `main` and confirm it appears in GitHub and is selectable.
- [ ] **Branching strategy (us-7.3):** set each of per-story / per-work-item / work-on-main and confirm the helper text (incl. the main-strategy "bypasses the PR review gate" note); later confirm a dispatched run's branch name is a **title-based** slug (`factory/us-1.4.1-<title>`), not a UUID.
- [ ] **Deployment Website + environment (us-7.2):** create a UAT deployment and a Production deployment; set environment; set a Website (test both a **domain** and an **IP** form); confirm the Website renders as a working external link on the deployment and on the release surfaces; confirm classified UAT/Prod deployments inherit the project release branch.

### C. Build config (us-7.9)
- [ ] Add build config key/values on Demo (e.g. a test DB URL, a sandbox key); confirm the UI shows only "Set · <when>", **never** the value; confirm no response or network payload echoes a value back.

### D. Epics & work-item identity (us-7.10, us-7.13)
- [ ] **Default & IDs (us-7.10):** Demo has Epic 1 active; create a feature and break it into stories, plus a bug and a chore; confirm IDs render as `FEAT-1.<n>`, `US-1.<n>.<m>`, `BUG-1.<n>`, `CHORE-1.<n>`; confirm a new item defaults into the active epic.
- [ ] **Close-gate (us-7.10):** with at least one open work item, attempt **Start new epic** and confirm it is **blocked** with the blockers named; resolve them (complete/abandon/delete); confirm starting Epic 2 then succeeds and new items number `…2.<n>`.
- [ ] **Projects card + views (us-7.13):** on the Projects page, Demo's card shows the active epic, the 3 latest work items (with IDs + status), and a **More** button that opens Work Items filtered to Demo; the Work Items **List** and **Table** views show each item's Epic.

### E. Build a work item end to end (us-7.15, us-7.3)
- [ ] Dispatch a Demo work item and, via the worker/MCP path, confirm the agent's work context carries the **submit mode** (PR vs direct-to-main per the strategy), the **readable ID**, the **build config values**, the **environment website**, and the **project summary**; confirm a PR-mode item opens a PR and a main-strategy item lands with **no** PR (review gate bypassed). *(Exercised by dispatching real work; MCP fields verified through the resulting run/PR behavior.)*

### F. Release, versioning & activity (us-7.14, us-7.11)
- [ ] **Cut a version (us-7.14):** cut a release and confirm the factory assigns `V1.1`, tags the release branch commit, and shows the version on the release ledger and the deployment; a second cut is `V1.2`; after starting Epic 2 and cutting again, it is `V2.1`.
- [ ] **Promotion (us-7.14):** promote UAT→Production and confirm the **same** version carries over (no re-version).
- [ ] **Activity feed (us-7.11, us-7.10):** confirm the activity feed now shows epic **opened/closed**, plus release **QA sign-off** and **production promotion** events, alongside deploys and merges.

### G. Help (us-7.12)
- [ ] Open Help and confirm it documents Epics (IDs, close-gate), the six-check setup/readiness, the Project Summary + brainstorm flow, branching strategy, environments/Website/versions/releases, build config, and Build-Mill-as-system-of-record — with the setup-stepper mirroring the readiness order and **no** stale GitHub-Issue-sync references.

## Part 2 — Agent end-to-end (headless Claude CLI worker)

One work item, all the way through, with two actors: the **manager** in the browser and the **agent** in a shell. Everything below is what the 2026-07-20 runs actually did, in order, including the traps that cost time.

### 2.1 Prerequisites — collect these before starting

| What | Where it comes from | Notes |
|---|---|---|
| Org shortname | `organizations.shortname` (Admin, or the DB) | **Not** a `slug` column — that column does not exist |
| Project slug | `projects.slug` for Demo | |
| Worker token | Team → the agent principal → register/rotate | **Shown once.** Only a hash and `token_last4` are stored; if it scrolls away, rotate — it cannot be recovered |
| Agent auth | `claude setup-token` → `CLAUDE_CODE_OAUTH_TOKEN` | An expired CLI session fails the run *before* the MCP is ever reached, with an error that does not mention auth |

The MCP endpoint is the single `https://api.buildmill.dev/mcp` — there is no org/project in the URL any more. Scope comes from the worker token itself (`workers.project_id`, assigned when the worker is created or edited); a worker with no project assigned sees an empty pool, so an empty result may mean the wrong project is assigned rather than a wrong URL.

### 2.2 Wire up the worker

Write an MCP config the CLI can consume (keep it outside the repo — it holds the token):

```json
{ "mcpServers": { "factory": {
    "type": "http",
    "url": "https://api.buildmill.dev/mcp",
    "headers": { "X-Worker-Token": "<worker-token>" } } } }
```

Prove the connection before dispatching anything — this one call distinguishes a bad token (401) from an empty pool (a valid, empty result):

```bash
claude -p "Call list_available_work and show the raw result." \
  --mcp-config <config-path> --strict-mcp-config \
  --allowed-tools mcp__factory --output-format text
```

**The allow-list is the single biggest trap.** `--allowed-tools` with an incomplete explicit list silently *removes* tools — the agent gets no error, the tool simply is not there. This bit both runs: one agent could not call `get_repo_tree` and split a PRD blind; another could not call `add_comment` and lost its findings to stdout. Pass the whole server (`mcp__factory`) unless you are deliberately testing a restricted grant, and if you do enumerate, include the tools below.

`--strict-mcp-config` keeps the operator's other MCP servers out of the run, so the test exercises the factory's surface only.

### 2.3 The worker loop

Every run kind follows the same shape. The agent should be told the loop, not the individual calls:

`list_available_work` → `get_instructions` → `claim_work` → `get_work_context` → *do the work* → `validate_submission` → `submit_*`

- **Reading the repo:** `get_workspace`, `get_repo_tree`, `read_repo_file`.
- **Talking to the manager:** `report_progress` (also extends the lease on long runs), `add_comment`, `request_clarification`.
- **Submitting:** `submit_prd`, `submit_stories`, `submit_plan`, `submit_changeset`, `submit_code_work`, `report_test_results`.
- **Giving up cleanly:** `release_work` — hand the run back rather than holding the claim to expiry.

### 2.4 The seven-step script

| # | Actor | Step | What to verify |
|---|---|---|---|
| 1 | Manager (browser) | Create a work item on Demo — a feature with enough substance to break down (the runs used "web app login + user management") | Readable ID assigned; item lands in the active epic; Things to Do shows it waiting on the factory |
| 2 | Agent (CLI) | Claim the **PRD** run and submit a PRD | The agent can read the repo *before* writing requirements (today it cannot — [us-13.2](completed/phase-13-agent-specialization-effectiveness.md)) |
| 3 | Manager | Review and approve the PRD, then route it for stories | Approval and "continue" are one action; the gate is on `/review/{id}`, not buried in the work item page |
| 4 | Agent | Claim the **breakdown** run and submit stories | Stories are materialized as child work items with `US-<epic>.<n>.<m>` ids |
| 5 | Manager | Approve the first story and dispatch its **plan** run; approve the plan | A feature cannot be planned directly — the guard should refuse it (us-11.2); plan approval materializes the test cases |
| 6 | Agent | Claim the **code** run, branch, write the code, submit | Branch name is a title-based slug, not a UUID; the agent reports what it did and did **not** verify (us-11.5); progress notes appear in the work item Timeline as text, not the words "Progress note" |
| 7 | Manager | Review the submission and merge the PR | PR opens against the configured branch; merge lands; the activity feed records it |

Run it end to end without helping the agent — the point is what it does unaided. A prompt that hints at the answer invalidates the check. The us-11.5 validation only counted because the prompt was deliberately *neutral* about test execution and the agent still reported honestly.

### 2.5 Watch for these — each one has bitten a real run

- **A silent stall looks exactly like work.** A code run legitimately took 18 minutes. Nothing distinguishes that from a dead worker; if a run goes quiet, check `issue_events` before assuming a hang ([us-13.6](completed/phase-13-agent-specialization-effectiveness.md), [us-13.7](completed/phase-13-agent-specialization-effectiveness.md)).
- **An empty repo 404s.** `get_repo_tree` returning "ref 'main' not found" can mean the branch is genuinely empty (an empty tree 404s), not that credentials are broken. `get_workspace` still works — check it before filing a bug.
- **The Live Runner Console does not cover MCP workers.** `/team/<principal>/runner` is built for supervisor runners with a WebSocket session; a headless worker reads "offline" forever, mid-run included. That is expected today, not a defect.
- **Check the DB when the UI is ambiguous.** `issue_events`, `runs`, and the claim/lease columns answer "did it actually happen" faster than re-reading a page.
- **Log defects as you go**, with the exact step and expected vs. actual. Both runs produced more findings than anyone remembered by the end.

### 2.6 Recording the result

- PASS means the item reached a **merged PR** without a human doing the agent's work for it.
- Every defect found becomes a story or a chip, with the run's evidence attached — that is how Phases 11–13 were written.
- Note which fixes were live at the time. A run against a `prod` that predates the fixes tests the old build and proves nothing about the new one.

## Acceptance criteria

- [ ] Each check in the Part 1 checklist is executed on https://app.buildmill.dev against Demo and recorded PASS/FAIL with evidence (screenshot or observed result).
- [ ] The Part 2 seven-step script completes on the deployed app with a headless agent, ending in a merged PR, with every defect logged.
- [ ] Failures are logged with exact step, expected vs. actual, and any console/network error, and triaged into fixes.
- [ ] The live end-to-end pass holds: Demo goes empty → Ready (six-for-six) → one dispatched item built → a version cut and promoted — with no GitHub Issue sync anywhere.

## Out of scope

- **us-7.1** complexity scoring (deferred; not built this round).
- Load / performance / security testing — this is functional UAT.
- Automated end-to-end test authoring — this is manual, browser- and CLI-driven verification.
- The git remote itself (clone/fetch/push through the proxy), which [Full Git Router QA](us-Full-Git-Router-QA.md) covers.
- Setting up Grok Build or OpenCode workers. Part 2 exercises the Claude CLI path, which is what the factory is driven with today.

## Features covered

- **Part 2** exercises the whole pipeline — dispatch, the MCP worker surface, all four run kinds, every manager gate, and the PR merge — so it implicitly covers whatever shipped most recently. The 2026-07-20 runs are what produced Phases 11, 12 and 13.
- The project setup & release stories — [us-7.2](completed/phase-07-project-setup-release-readiness-factory-intelligence.md), [us-7.3](completed/phase-07-project-setup-release-readiness-factory-intelligence.md), [us-7.4](completed/phase-07-project-setup-release-readiness-factory-intelligence.md), [us-7.5](completed/phase-07-project-setup-release-readiness-factory-intelligence.md), [us-7.6](completed/phase-07-project-setup-release-readiness-factory-intelligence.md), [us-7.7](completed/phase-07-project-setup-release-readiness-factory-intelligence.md), [us-7.8](completed/phase-07-project-setup-release-readiness-factory-intelligence.md), [us-7.9](completed/phase-07-project-setup-release-readiness-factory-intelligence.md), [us-7.10](completed/phase-07-project-setup-release-readiness-factory-intelligence.md), [us-7.11](completed/phase-07-project-setup-release-readiness-factory-intelligence.md), [us-7.12](completed/phase-07-project-setup-release-readiness-factory-intelligence.md), [us-7.13](completed/phase-07-project-setup-release-readiness-factory-intelligence.md), [us-7.14](completed/phase-07-project-setup-release-readiness-factory-intelligence.md), [us-7.15](completed/phase-07-project-setup-release-readiness-factory-intelligence.md) — this round verifies them all live
