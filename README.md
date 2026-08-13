# Software Factory — Build Plan

A custom application that orchestrates AI agents (Architect, Programmer, QA, Release/Infra) through the full software delivery lifecycle, with you as the human Manager approving decisions and answering agent questions from a central queue.

## Getting started

```bash
npm install          # install workspace dependencies
npm run dev          # start the web app → http://localhost:3000
```

Copy `apps/web/.env.local.example` to `apps/web/.env.local` and fill in your Supabase project URL and publishable key. See [ARCHITECTURE.md](ARCHITECTURE.md) for how the pieces fit together and [stories/users.md](stories/users.md) for the build plan. For what the application actually does today — its interfaces, domain objects, and lifecycles — see [APPLICATION.md](APPLICATION.md).

---

## 1. Core Concept

The system is a **stateful workflow engine** wrapped around AI agents. Every unit of work (feature, bug, release) is a **Task** that moves through a state machine. Agents execute stages; when they need a human decision, they pause, write a **Decision Request** to a queue, and resume when you answer.

```
[New Task] → Architect → (Manager approval) → Programmer → QA → Release Bundling → UAT Deploy → (Manager UAT sign-off) → Production
                 ↑______________ questions/answers to Manager at any stage ______________↑
```

Key design principle: **agents never block your attention synchronously.** All questions land in a queue you review on your own schedule; answers are routed back into the agent's context and the workflow resumes automatically.

---

## 2. System Components

### 2.1 Workflow Orchestrator
- Built on **LangGraph** (Python) — its checkpointing + interrupt features are purpose-built for "pause for human input, then resume."
- Each Task is a graph execution. Nodes = agent stages. Edges = transitions based on stage outcome (approved / rejected / needs-clarification).
- Graph state is checkpointed to Postgres, so a task can sit paused for days waiting on your answer and resume exactly where it left off.

### 2.2 Agent Runtime
- Each role (Architect, Programmer, QA, Release) is an agent definition: system prompt + tools + autonomy policy.
- Agents run via the **Claude API** (or Claude Code in headless/SDK mode for the Programmer agent, since it needs to clone repos, edit code, run tests, and open PRs).
- Programmer/QA agents run inside sandboxed containers with the target repo checked out.

### 2.3 Decision & Clarification Queue (the piece GitHub couldn't do)
- A `decisions` table: every agent question or approval request becomes a record with full context (task, stage, agent's reasoning, options proposed, artifacts attached).
- Your Manager UI shows these as an inbox. You can batch-review, answer inline, approve/reject, or send back with comments.
- Answering a decision fires a webhook/event that resumes the paused LangGraph execution with your answer injected into agent context.

### 2.4 Artifact Store
- User stories, architecture docs, wireframes, test plans, release notes — stored as versioned records linked to the Task (Postgres + object storage for files).
- Code artifacts stay in GitHub (PRs, branches); the app stores links + PR status via GitHub webhooks.

### 2.5 Manager UI (your control panel)
- **Inbox**: pending decisions/questions, sorted by priority and age.
- **Board**: all tasks by stage (Kanban-style: Requirements → Dev → QA → Release → UAT → Prod).
- **Task detail**: full history — every agent action, question, answer, artifact, PR link.
- **Agent tuning panel**: edit autonomy policies and prompts per agent (see §5).

### 2.6 Integrations
- **GitHub**: repo access (via GitHub App), PR creation, status checks, webhooks for CI results.
- **CI/CD**: GitHub Actions triggers for UAT and Production deploys; the Release agent composes releases and triggers pipelines.
- **Notifications**: push/email/Slack when new decisions hit your queue.

---

## 3. Data Model (core tables)

| Table | Purpose |
|---|---|
| `projects` | Product + its 3 linked GitHub repos, environment configs |
| `tasks` | Feature/bug/release; type, priority, current stage, status |
| `task_events` | Append-only log of everything that happened on a task |
| `decisions` | Pending/answered manager decisions; question, context, options, answer, timestamps |
| `artifacts` | User stories, arch docs, test plans, wireframes; versioned, linked to task |
| `agent_runs` | Each agent execution: role, input state, output, tokens/cost, checkpoint ref |
| `agent_policies` | Per-agent autonomy rules and prompt overrides (see §5) |
| `releases` | Bundles of tasks/PRs; UAT status, prod status, sign-offs |

---

## 4. Workflow State Machines

### 4.1 Feature flow
1. **Intake** — you create the feature task.
2. **Architecture** — Architect agent studies the repos, produces: requirements doc, user stories, architecture notes, wireframe descriptions. → submits **approval decision** to you.
3. **Development** — on approval, Programmer agent picks up stories, works in a branch, opens PR(s). Questions go to your queue; otherwise it proceeds.
4. **QA** — QA agent writes/runs tests against the PR branch, files findings. Fails loop back to Programmer automatically (bounded retries, then escalate to you).
5. **Release bundling** — Release agent groups approved PRs into a release, writes release notes, merges, deploys to **UAT**.
6. **UAT sign-off** — decision request to you: "Release X is on UAT, verify and approve."
7. **Production** — on your approval, Release agent triggers prod deploy, monitors, closes the task.

### 4.2 Bug flow (higher autonomy)
1. Bug reported (by you, or later via an intake integration).
2. Architect agent triages: analyzes root cause, defines fix approach + user story — **no approval gate** (per policy), goes straight to Programmer.
3. Programmer fixes, tests, PR → QA verifies → auto-deploy to UAT.
4. Single decision to you: "Bug #123 fixed and on UAT — verify and approve for prod."

Every stage transition and gate is data-driven (stored in the workflow definition), so you can add/remove approval gates without code changes.

---

## 5. Autonomy Tuning (decide vs. ask)

Each agent has a policy object you edit from the UI:

```yaml
architect:
  ask_manager_when:
    - requirement_ambiguity: high        # conflicting or missing requirements
    - scope_change: any                   # anything expanding original scope
    - breaking_change: any
  decide_alone_when:
    - tech_choice_within_existing_stack: true
    - bug_triage: severity <= high
  max_questions_per_task: 5              # forces consolidation into batched questions
programmer:
  ask_manager_when:
    - api_contract_change: any
    - dependency_addition: new_major_lib
    - estimated_effort_exceeds: 2x_original
  decide_alone_when:
    - implementation_details: true
    - refactors_under_n_files: 10
```

Implementation: the policy is compiled into the agent's system prompt + a `request_decision` tool the agent calls. The tool schema forces the agent to state the question, its own recommendation, and options — so your inbox items are answerable in one tap.

---

## 6. Tech Stack

| Layer | Choice | Why |
|---|---|---|
| Orchestration | LangGraph (Python) + Postgres checkpointer | Native pause/resume, human-in-the-loop interrupts |
| Agents | Claude API; Claude Code SDK (headless) for coding agent | Your existing skills/prompts port over |
| Backend | FastAPI | Async, plays well with LangGraph |
| DB | Postgres | State, decisions, artifacts metadata |
| Queue/events | Redis + worker processes (or Postgres LISTEN/NOTIFY to start) | Resuming runs on decision-answered events |
| Sandbox | Docker containers per agent run | Programmer/QA need isolated repo checkouts |
| Frontend | React (Next.js) | Inbox, board, task detail, tuning panel |
| Auth | Single-user to start; add roles later | You're the only manager initially |
| GitHub | GitHub App + webhooks | PRs, CI status, repo access with scoped permissions |

---

## 7. Build Phases

### Phase 1 — Skeleton (2–3 weeks)
- Postgres schema, FastAPI backend, minimal React UI.
- Task CRUD + state machine for ONE flow (bug flow — it's the shortest).
- Single agent (Architect) with the `request_decision` tool; decision inbox UI; answer → resume loop working end-to-end.
- **Milestone: create a bug, agent triages it, asks you a question, you answer in the UI, agent completes triage.**

### Phase 2 — Coding loop (3–4 weeks)
- Programmer agent via Claude Code SDK in a Docker sandbox; GitHub App integration (branch, commit, PR).
- QA agent: test writing/execution, PR review, bounded fail→fix loop with Programmer.
- **Milestone: a real bug in one of your repos gets fixed and a PR opened with zero manual coding.**

### Phase 3 — Release pipeline (2–3 weeks)
- Release agent: PR bundling, release notes, UAT deploy via GitHub Actions.
- UAT sign-off decision flow; prod deploy on approval.
- **Milestone: bug flow runs end-to-end from report → prod, with only your two approvals.**

### Phase 4 — Feature flow + tuning (2–3 weeks)
- Full architect stage (user stories, wireframes, arch docs) with approval gate.
- Autonomy policy engine + tuning UI.
- Kanban board, task history views, notifications.

### Phase 5 — Hardening (ongoing)
- Cost tracking per task (token spend in `agent_runs`).
- Retry/timeout handling, dead-letter queue for stuck runs.
- Metrics: cycle time per stage, questions-per-task (to measure autonomy tuning).
- Multi-project support, external bug intake (email/form/webhook).

---

## 8. Key Risks & Mitigations

- **Agent gets stuck in loops** → hard caps on iterations per stage, auto-escalate to your queue.
- **Context loss on long tasks** → artifacts + task history are re-injected as structured context on every agent run; don't rely on one long conversation.
- **Runaway cost** → per-task token budgets; agent run halts and escalates when exceeded.
- **Bad merges to prod** → prod deploy is always behind your explicit UAT sign-off; keep it that way even as autonomy grows.
- **GitHub rate limits / CI flakiness** → treat all external calls as retryable jobs, never inline in agent reasoning.

---

## 9. First Concrete Steps

1. Stand up repo + Postgres + FastAPI + Next.js scaffold.
2. Define the `tasks`, `decisions`, `agent_runs` tables.
3. Build the LangGraph bug-flow graph with one interrupt node (`request_decision`).
4. Wire the inbox UI to answer decisions and resume the graph.
5. Port your existing Claude Code "stories" skills into the Architect agent's prompt/tools.
