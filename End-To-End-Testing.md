# End-to-End Testing — requirement → PR merge

A full round of Build Mill's delivery pipeline, exercised as both roles: the **manager** driving the web UI and an **agent** (Claude Code) working entirely over the Factory MCP server. The goal is to go from a raw requirement to a merged PR with every gate crossed for real — no simulated steps.

This documents the round performed on **2026-07-17** (result: **PASSED** — [PR #1](https://github.com/Nikesh-Consulting-LLC/notes/pull/1) squash-merged as `1f4b8a9`), and doubles as the runbook for repeating it. Issues found are listed at the end with the stories they produced. A second round the same evening exercised the **git-free loop** those stories built — see [Round 2](#round-2--the-git-free-loop-2026-07-17-evening) at the bottom (also **PASSED** — [PR #2](https://github.com/Nikesh-Consulting-LLC/notes/pull/2) merged as `d649991`, no agent-side git, no merge override).

---

## Scope

What a full round proves, end to end:

1. A requirement entered in the UI becomes a dispatched **PRD run**.
2. An agent claims it over MCP, writes the PRD, and submits it into the **PRD gate**.
3. The manager approves; the feature breaks down into **stories**.
4. The agent claims the **plan run**, submits an implementation plan + test plan; approval **materializes test cases**.
5. The agent claims the **code run**, implements, verifies, and submits; the factory opens the **PR**.
6. The manager reviews and approves; the PR **merges**; the story and feature complete automatically.

## Test assets

| Asset | Value used in this round |
|---|---|
| Project | **Audio notes** (repo `Nikesh-Consulting-LLC/notes`) |
| Requirement | Transcript cleaner: strip filler words, normalize whitespace, split sentences, plus a CLI |
| Worker | "Claude Code" (this session), type autonomous |
| Local stack | web `http://localhost:3000`, API `http://localhost:8000` (uvicorn) |
| Hosted stack | `https://app.buildmill.dev` / `https://api.buildmill.dev` — **same Supabase DB as local**, so workers, tokens, and claims are interchangeable |
| Local checkout | `D:\Github\notes` (used per instruction instead of cloning the factory remote — same repo) |

## Preconditions

- [ ] Web and API running (`npm run dev`; `uvicorn app.main:app` from `apps/api` with its `.venv`).
- [ ] An **LLM provider** configured for the org (breakdown proposal is a server-side LLM call).
- [ ] A **GitHub connection** for the org whose App credentials in `apps/api/.env` match the installation (`GITHUB_APP_ID` / `GITHUB_APP_SLUG` / private key — see [Issues found](#issues-found), this is exactly what bit us).
- [ ] The project linked to a real repo with a default branch.
- [ ] A registered worker with its token in hand (shown once at registration; regenerate if lost).

## MCP connection

Register the worker (Settings → Workers → Register worker) and assign it a project — its MCP scope now comes from the worker itself, not the URL — copy the one-time token, then connect any MCP-capable tool:

```
claude mcp add --transport http factory https://api.buildmill.dev/mcp \
  --header "X-Worker-Token: <your-worker-token>"
```

Notes from this round:

- The server is **stateless streamable-HTTP with JSON responses** — raw JSON-RPC POSTs (`tools/list`, `tools/call`) work without an `initialize` handshake, which makes scripted driving (curl/Python) easy.
- The hosted API sits behind a WAF that 403s Python's default `urllib` User-Agent — set a real `User-Agent` header when scripting.

---

## The round, step by step

Steps alternate actor. **M** = manager in the web UI, **A** = agent over MCP.

### Phase 1 — Setup

| # | Actor | Action | Expected | Observed |
|---|---|---|---|---|
| 1 | M | Start web + API; open the app | Both up; login works | ✅ |
| 2 | M | Open project **Audio notes** | Project page with board, guidelines, connect | ✅ |
| 3 | M | Register worker "Claude Code" (autonomous); copy one-time token | Token shown exactly once; row shows `…last4` | ✅ |
| 4 | A | Connect to the project-scoped MCP URL with the token; `tools/list` | Tool surface listed; worker row shows *Last seen just now* | ✅ |

### Phase 2 — Requirement → PRD

| # | Actor | Action | Expected | Observed |
|---|---|---|---|---|
| 5 | M | Add a new requirement (feature) on the frontend; **Draft PRD** | A `prd` run appears in the pool | ✅ |
| 6 | A | `list_available_work` → `claim_work` → `get_work_context` | Raw idea + instructions in context; lease held | ✅ |
| 7 | A | Write the PRD (exactly four sections: `## Problem`, `## Goals`, `## Out of scope`, `## Acceptance criteria`) → `submit_prd` | Issue moves to **prd-review** | ✅ |
| 8 | M | Review the PRD; approve with breakdown **Single story** | PRD approved; one child story created | ✅ (Base UI select needed a coordinate click — ref-click closed the dialog) |
| 9 | M | Accept the proposed story | Story accepted, ready for planning | ✅ |

### Phase 3 — Plan

| # | Actor | Action | Expected | Observed |
|---|---|---|---|---|
| 10 | A | Claim the **plan** run; `get_work_context` (story, AC, PRD, guidelines) | Full planning context | ✅ |
| 11 | A | `submit_plan` with implementation plan + **test plan carrying a ```json fence**: `{"cases": [{title, steps, expected_result, test_types}]}` | Issue moves to **plan-review** | ✅ |
| 12 | M | Approve the plan | Test plan **materializes into test cases** (9 created); a `code` run dispatches | ✅ |

The JSON fence format matters: a test plan without a parseable fence materializes **zero** cases silently. (Fixed since — see us-5.21.)

### Phase 4 — Code

| # | Actor | Action | Expected | Observed |
|---|---|---|---|---|
| 13 | A | Claim the **code** run; work in the checkout: `transcript_cleaner` package (filler removal, whitespace normalization, sentence splitting), CLI (`-o`, stderr + exit 1 on unreadable input), `pyproject.toml`, README | Working package | ✅ 18 pytest tests passing |
| 14 | A | Push `factory/issue-<id>` and `submit_code_work(run_id, branch_ref, notes, stdout)` | Factory verifies the branch, opens the PR, issue → **in-review** | ❌ locally, ✅ hosted — see below |

**The local failure (worth documenting in full):** `submit_code_work` against the local API answered *"no GitHub connection for this org — push the branch to the factory remote first."* The branch was fine and pushing was irrelevant. API logs showed the real cause: `could not mint installation token (404)` — the installation id stored for the org belongs to the **build-mill** GitHub App (`4318106`), while `apps/api/.env` carried the **dev app's** credentials (`nexdb-software-factory-dev`, `4291723`). A 404 on token mint means "this installation does not belong to the authenticated App." Since both stacks share one database, the fix for the round was simply to submit through the **hosted** MCP instead, which holds the right credentials. The local `.env` still needs the build-mill App id/slug/PEM.

| # | Actor | Action | Expected | Observed |
|---|---|---|---|---|
| 15 | A | Resubmit via hosted MCP (`api.buildmill.dev`) | PR opened by the factory | ✅ PR #1 |

### Phase 5 — Review → merge

| # | Actor | Action | Expected | Observed |
|---|---|---|---|---|
| 16 | M | Open the review: diff vs story, test strip, notes | Diff from GitHub; 9 linked cases shown | ✅ but all 9 cases counted **unrun** |
| 17 | M | Approve — requires a **merge override** (unrun tests) with a recorded reason | Squash merge; story → Merged; feature auto-completes | ✅ PR #1 squash-merged as `1f4b8a9` |

The override was the round's biggest wart: the agent had made all 9 cases pass via pytest, but had no channel to say so — its only voice was prose in `stdout`. (Fixed since — see us-5.19.)

**Result: PASSED.** Requirement → PRD gate → breakdown → plan gate (cases materialized) → code → factory-opened PR → review → squash merge, with the story and parent feature closing automatically.

---

## Issues found

Every finding became a story; all nine were subsequently **built** (commits `1f8b849` … `f181fbd`, in `Testing` awaiting UAT).

| Finding during the round | Story |
|---|---|
| Credential/installation mismatch flattened into "no GitHub connection — push the branch first"; real cause only visible in API logs | [us-5.24](stories/us-5.24-actionable-github-errors.md) — error taxonomy: who broke ≠ who fixes; hints never say "push" for a credential failure |
| Worker verified all 9 test cases but the review still demanded a merge override | [us-5.19](stories/us-5.19-mcp-report-test-results.md) — `report_test_results` per `test_case_id`; agent-verified passes lift the gate |
| Plan run told to "study the repository first" with no way to see the repo over MCP | [us-5.20](stories/us-5.20-mcp-repo-browsing.md) — `get_repo_tree` / `read_repo_file` |
| The test-plan JSON fence is a prose convention; a malformed one materializes 0 cases silently (the worker only got it right by reading the parser's source) | [us-5.21](stories/us-5.21-mcp-validate-submission.md) — `validate_submission` sharing the gate's parser; submit warnings; manager banner |
| After submit, the agent is blind to CI/mergeability/review comments | [us-5.22](stories/us-5.22-mcp-pr-status.md) — `get_pr_status` |
| Toolchain guessing: guidelines said "pytest" in prose, nothing named the runtime or setup | [us-5.23](stories/us-5.23-structured-environment-context.md) — structured environment in the work context |
| Whole design gap: agents shouldn't need git at all | [us-5.25](stories/us-5.25-mcp-workspace-snapshot.md) + [us-5.26](stories/us-5.26-mcp-changeset-submission.md) + [us-5.27](stories/us-5.27-git-free-agent-onboarding.md) — git-free loop: `get_workspace` → work → `submit_changeset` (server-side commit/PR), Connect page leads with "Agents — MCP only" |

Environment quirks (not product bugs, but they cost time):

- `apps/api/.env` on the local machine still carries the dev App's GitHub credentials; the org's installation belongs to build-mill (`4318106`). Until fixed, local `submit_code_work` (and every GitHub-touching call) fails while the hosted stack works.
- The hosted MCP deployment lagged the code at test time (only the core 8 tools; no `list_my_work`). Redeploy the API before a round that exercises newer tools.
- WAF on `api.buildmill.dev` rejects Python `urllib`'s default User-Agent — set a custom one when scripting.

## The next round

The nine stories built from these findings change the happy path. A future round should exercise the **git-free loop**, which collapses steps 13–17 into:

```
claim_work → get_work_context        (environment + test_case_ids included)
          → get_repo_tree / read_repo_file   (study the repo)
          → get_workspace            (zip pinned to base_sha — no clone)
          → work locally             (setup commands from the environment block)
          → validate_submission      (dry-run the gate's checks)
          → submit_changeset         (factory builds commit/branch/PR)
          → report_test_results      (agent-verified passes — no merge override)
          → get_pr_status            (checks, mergeability, review comments)
```

Expected differences from this round: no git tooling on the agent side, no credential ambiguity (errors name the fix owner), no silent zero-case test plans, and no merge override when the agent has verified every case.

---

## Round 2 — the git-free loop (2026-07-17 evening)

Result: **PASSED** — [PR #2](https://github.com/Nikesh-Consulting-LLC/notes/pull/2) merged as `d649991`; the story (`Add transcript statistics feature with CLI command`) went requirement → merge with **zero git commands on the agent side** and **no merge override**. This round is the UAT evidence for us-5.19–us-5.27.

### Setup differences from round 1

| Item | Round 2 value |
|---|---|
| Stack | **Hosted only** (`app`/`api.buildmill.dev`). Local `.env` still carries the dev App's GitHub credentials, so the hosted stack (build-mill App) is the working path. |
| Deploy | Release [PR #23](https://github.com/Nikesh-Consulting-LLC/software-factory/pull/23) main→prod merged first — the runbook's "redeploy the API before a round that exercises newer tools" step. After deploy, `tools/list` served all 26 tools. |
| Requirement | Transcript statistics: five stats derived from the existing cleaning pipeline, `transcript-stats` CLI with `--json` and stdin modes |
| Worker | "Claude Code (Fable session)" — token recovered via Settings → Workers → **Show** (us-3.20), no regenerate needed |
| Environment block | Configured first (Guidelines tab → Environment card, us-5.23): Python 3.12, `pip install -e .` + `pytest`, notes |

### The loop as it actually ran

| # | Actor | Action | Observed |
|---|---|---|---|
| 1 | M | Connect page check | ✅ us-5.27: leads with "Agents — MCP only" (MCP URL + token, "no git required", names `get_workspace`/`submit_changeset`); git-native section demoted below |
| 2 | M | New work item → **Draft PRD** | ✅ prd run pooled |
| 3 | A | `claim_work` → `get_work_context` → PRD → `validate_submission` → `submit_prd` | ✅ us-5.21 dry-run: "structurally sound, 0 findings"; environment block already rides the guidelines on prd runs |
| 4 | M | Approve PRD, breakdown **Single story**, accept | ✅ same Base UI select quirk as round 1 (coordinate click) |
| 5 | M | Dispatch planning on the story | ✅ plan run pooled |
| 6 | A | Claim → `get_repo_tree` + `read_repo_file` (all 8 files) → plan + test plan (12-case JSON fence) → `validate_submission` → `submit_plan` | ✅ us-5.20: tree + files served with ref defaulting; us-5.21 validated the fence parses |
| 7 | M | Approve plan | ✅ **12/12 cases materialized** — exact parity with the fence (us-5.21's shared parser) |
| 8 | M | **Dispatch code** (button on the story) | ✅ — note: round 1's table said the code run dispatches on plan approval; in reality plan approval leaves the story `planned` and the manager clicks Dispatch code |
| 9 | A | Claim → context has `environment` **and 12 `test_case_ids`** → `get_workspace` | ✅ us-5.23 + us-5.19 context halves; us-5.25: 7.4 KB zip pinned to `base_sha 1f4b8a9` (= then-HEAD), no `.git` |
| 10 | A | Implement locally per the environment block; `pytest -q` | ✅ 30 passed (18 pre-existing + 12 new); installed `transcript-stats` exercised manually in both modes + stdin + error path |
| 11 | A | `submit_changeset` (7 files: 4 add, 3 update, declared base_sha) | ✅ us-5.26: server-side commit `90dc094` on `factory/issue-…`, PR #2 opened, issue → in-review |
| 12 | A | `report_test_results` — 12 results with per-case evidence | ✅ us-5.19: "recorded 12 — the review page now shows them as agent-verified" |
| 13 | A | `get_pr_status` | ❌ **"could not list checks (403)"** — see findings |
| 14 | M | Review | ✅ test strip: **"12 passing · 12 agent-verified"**, diff from GitHub, AC checklist |
| 15 | M | Approve | ✅ plain "Approve and merge?" — **no override, no reason field** (round 1's biggest wart is gone) |
| 16 | — | Post-merge | ✅ story → `merged`, parent feature auto-completed → `done` |

### Findings

| Finding | Detail / suggested story |
|---|---|
| `get_pr_status` hard-fails on checks 403 | PR itself was open and `mergeable_state: clean`, and the repo has zero check runs — but the **build-mill App's installation token gets 403 on the check-runs API** (App lacks Checks: read). The tool dies instead of degrading to "PR state + mergeability, checks unavailable", and the hint says *"check the path/ref and retry"* — a worker-fix hint for a manager-fix permission problem, exactly the flattening us-5.24 exists to prevent. Fix both: grant the App Checks (read), and make the tool degrade + classify 403 as credentials/permissions. |
| Connect page **Regenerate** button froze the tab | Clicked twice (two fresh tabs): the renderer hung ~30 s+, the mutation never fired (`token_last4` unchanged). Settings → Workers **Show**/Regenerate works fine. Observed under browser automation (CDP), so a human repro is unconfirmed — but twice-for-twice on that one button smells like a client-side hang worth a look. |
| Round-1 record correction | Plan approval does **not** auto-dispatch the code run; the story sits `planned` until the manager clicks **Dispatch code** (step 8 above). Round 1's step 12 glossed this. |

Environment quirks carried over: local `apps/api/.env` still needs the build-mill App credentials; the WAF still requires a real `User-Agent` (the driver script sets one).
