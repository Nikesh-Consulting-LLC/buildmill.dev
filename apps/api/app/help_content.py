"""US-2.30: factory-default text for the in-app operator handbook (/help).

Structure is code, words are data: the help page's diagrams — sections,
stages, nodes, links — live in the web app; every *descriptive* text unit
resolves from a `help/*` key in llm_prompt_templates with these defaults.
The web mirrors the same defaults in help-content.ts as its RPC-failure
fallback (the guidelines-catalog mirror pattern) — keep the two in sync.
"""

# key suffix (after "help/") -> (label, description, default text)
_ENTRIES: dict[str, tuple[str, str, str]] = {
    "overview/intro": (
        "Help — Overview caption",
        "Help page · caption under the big-picture flow.",
        "Build Mill turns a written story into a merged pull request. You "
        "define the work and make the calls; the factory plans, codes, and "
        "ships between your decisions.",
    ),
    "pipeline/intro": (
        "Help — Pipeline intro",
        "Help page · Pipeline section intro line.",
        "Every dispatchable work item walks the same rail. Work items live "
        "inside an epic — the numbering root: one epic is active at a time, "
        "new items land in it, and you close it and start the next only once "
        "every item is completed, deployed, abandoned, or deleted. Each item "
        "carries a readable, epic-scoped id (FEAT-1.4, US-1.4.1, BUG-1.5). "
        "Features first expand into a PRD and stories — then each story walks "
        "the rail itself. Requirements live in Build Mill; GitHub Issue sync "
        "is retired. Click a stage to see what happens there.",
    ),
    "pipeline/draft": (
        "Help — Pipeline: Draft stage",
        "Help page · Pipeline walkthrough, Draft detail card.",
        "You write the story: what to build, and acceptance criteria that "
        "make “done” testable. Attach documents worth reading. "
        "Dispatch planning when the story is ready.",
    ),
    "pipeline/plan": (
        "Help — Pipeline: Plan stage",
        "Help page · Pipeline walkthrough, Plan detail card.",
        "The agent studies the repository and writes an implementation plan "
        "and a test plan — no code yet. Both land for your review; "
        "approving them unlocks the build.",
    ),
    "pipeline/build": (
        "Help — Pipeline: Build stage",
        "Help page · Pipeline walkthrough, Build detail card.",
        "The agent implements the approved plan and pushes through the "
        "factory's git remote. How it branches follows the project's "
        "development strategy — a branch per story, per work item, or "
        "straight to main. PR modes wait for your review; a main-strategy "
        "project commits directly and bypasses the PR gate.",
    ),
    "pipeline/uat": (
        "Help — Pipeline: UAT stage",
        "Help page · Pipeline walkthrough, UAT detail card.",
        "The merged change deploys to UAT from the project's UAT release "
        "branch. You open the environment at its Website, try it against the "
        "story, and record QA sign-off — or send it back.",
    ),
    "pipeline/release": (
        "Help — Pipeline: Release stage",
        "Help page · Pipeline walkthrough, Release detail card.",
        "You approve promotion and trigger the Production deploy from the "
        "Production release branch. Cutting a release mints a system-computed "
        "version V<epic>.<release-seq> and git-tags it; promotion reuses the "
        "same version. Releases are tracked per environment with one-click "
        "rollback, and QA sign-offs and promotions show in the activity feed.",
    ),
    "lifecycle/intro": (
        "Help — Lifecycle intro",
        "Help page · Full lifecycle section intro line.",
        "Every path a work item can take — the happy path down the middle, "
        "and every approval, rejection, retry, and cancel around it. Amber "
        "is you; blue is the factory; green is shipped code.",
    ),
    "architecture/intro": (
        "Help — Architecture intro",
        "Help page · Architecture section intro line.",
        "Two trust zones: the cloud coordinates, while code checkouts and "
        "provider credentials stay on the operator's side. Click a "
        "component to see what it does.",
    ),
    "architecture/web": (
        "Help — Architecture: Web app",
        "Help page · Architecture map, Web app node panel.",
        "The app you're using now. It talks straight to Supabase for data "
        "under row-level security, and to the API only for orchestration.",
    ),
    "architecture/api": (
        "Help — Architecture: API",
        "Help page · Architecture map, API node panel.",
        "Thin FastAPI orchestrator: dispatch, worker callbacks, GitHub "
        "operations, LLM calls, and the factory git remote workers push "
        "through. The only component that can read stored server "
        "credentials.",
    ),
    "architecture/supabase": (
        "Help — Architecture: Supabase",
        "Help page · Architecture map, Supabase node panel.",
        "System of record: auth, Postgres, storage, realtime. Every table "
        "is org-scoped under row-level security; live status updates flow "
        "from here.",
    ),
    "architecture/workers": (
        "Help — Architecture: Workers",
        "Help page · Architecture map, Workers node panel.",
        "Anything that claims work and hands it back: the autonomous "
        "runner, or a person driving an IDE agent over MCP. Workers clone "
        "and push via the factory's git remote — no GitHub credentials on "
        "any worker.",
    ),
    "architecture/github": (
        "Help — Architecture: GitHub",
        "Help page · Architecture map, GitHub node panel.",
        "Source of truth for code. Branches, PRs, and CI live there; the "
        "factory stores links and mirrored status, never copies of code.",
    ),
    "statuses/intro": (
        "Help — Statuses intro",
        "Help page · Statuses & colors section intro line.",
        "Every badge in the app, decoded.",
    ),
    "status/draft": (
        "Help — Status: Draft",
        "Help page · status legend, one-line meaning.",
        "Being written; nothing dispatched yet.",
    ),
    "status/prd-review": (
        "Help — Status: PRD review",
        "Help page · status legend, one-line meaning.",
        "A drafted PRD awaits your approval.",
    ),
    "status/ready": (
        "Help — Status: Ready",
        "Help page · status legend, one-line meaning.",
        "PRD approved; ready to break into stories.",
    ),
    "status/planning": (
        "Help — Status: Planning",
        "Help page · status legend, one-line meaning.",
        "The agent is writing the implementation and test plans.",
    ),
    "status/plan-review": (
        "Help — Status: Plan review",
        "Help page · status legend, one-line meaning.",
        "The plans await your review.",
    ),
    "status/planned": (
        "Help — Status: Planned",
        "Help page · status legend, one-line meaning.",
        "Plan approved; ready to dispatch the code run.",
    ),
    "status/queued": (
        "Help — Status: Queued",
        "Help page · status legend, one-line meaning.",
        "Waiting for a worker to claim it.",
    ),
    "status/running": (
        "Help — Status: Running",
        "Help page · status legend, one-line meaning.",
        "A worker is on it right now.",
    ),
    "status/needs-fixes": (
        "Help — Status: Needs fixes",
        "Help page · status legend, one-line meaning.",
        "Rejected in review; dispatch a fix run carrying your feedback.",
    ),
    "status/in-review": (
        "Help — Status: In review",
        "Help page · status legend, one-line meaning.",
        "The diff is ready and waiting on your review.",
    ),
    "status/merged": (
        "Help — Status: Merged",
        "Help page · status legend, one-line meaning.",
        "Approved and merged to the default branch.",
    ),
    "status/done": (
        "Help — Status: Done",
        "Help page · status legend, one-line meaning.",
        "Released and closed out.",
    ),
    "status/succeeded": (
        "Help — Status: Succeeded",
        "Help page · status legend, one-line meaning.",
        "The run finished cleanly.",
    ),
    "status/cancelled": (
        "Help — Status: Cancelled",
        "Help page · status legend, one-line meaning.",
        "Stopped on request before finishing.",
    ),
    "status/failed": (
        "Help — Status: Failed",
        "Help page · status legend, one-line meaning.",
        "The run errored; fix the cause and re-dispatch.",
    ),
    "stage-state/complete": (
        "Help — Stage state: Complete",
        "Help page · stage-state legend, one-line meaning.",
        "This stage is finished.",
    ),
    "stage-state/in-progress": (
        "Help — Stage state: In progress",
        "Help page · stage-state legend, one-line meaning.",
        "The factory is working; nothing needed from you.",
    ),
    "stage-state/waiting": (
        "Help — Stage state: Waiting",
        "Help page · stage-state legend, one-line meaning.",
        "Paused on you — review, sign off, or dispatch.",
    ),
    "stage-state/failed": (
        "Help — Stage state: Failed",
        "Help page · stage-state legend, one-line meaning.",
        "The stage's run errored; re-dispatch to retry.",
    ),
    "stage-state/not-started": (
        "Help — Stage state: Not started",
        "Help page · stage-state legend, one-line meaning.",
        "Not reached yet.",
    ),
    "stage-state/not-tracked": (
        "Help — Stage state: Not tracked",
        "Help page · stage-state legend, one-line meaning.",
        "No deployment is classified for this environment, so the stage "
        "can't be tracked.",
    ),
    "setup/intro": (
        "Help — Setup intro",
        "Help page · Setup guides section intro line.",
        "First get a project ready to run the factory, then the connections "
        "behind it. Each step links to the page where it happens.",
    ),
    "setup/project/1": (
        "Help — Setup project, step 1",
        "Help page · Project setup stepper.",
        "Write a Project Summary on the Overview tab — what the project is "
        "and its goals. Then write the Agent Instructions and, on Task "
        "Instructions, the per-kind instructions agents are given.",
    ),
    "setup/project/2": (
        "Help — Setup project, step 2",
        "Help page · Project setup stepper.",
        "Connect the repository and pick the UAT and Production release "
        "branches (create them from main without leaving Build Mill), plus a "
        "development branching strategy.",
    ),
    "setup/project/3": (
        "Help — Setup project, step 3",
        "Help page · Project setup stepper.",
        "Review the Agent Instructions — including how releases are versioned — and mark them ready. Edit later and an “edited since ready” nudge "
        "appears.",
    ),
    "setup/project/4": (
        "Help — Setup project, step 4",
        "Help page · Project setup stepper.",
        "Review the Task Instructions per run kind and mark them ready too.",
    ),
    "setup/project/5": (
        "Help — Setup project, step 5",
        "Help page · Project setup stepper.",
        "Define the build & test config an agent runs against — the runtime "
        "and setup, the build/test/lint commands, and any write-only "
        "sandbox/test config values it needs to verify its work.",
    ),
    "setup/project/6": (
        "Help — Setup project, step 6",
        "Help page · Project setup stepper.",
        "Define a UAT and a Production deployment, each with a reachable "
        "Website. The Overview readiness checklist then reads six-for-six — "
        "Ready means an agent has everything it needs to build.",
    ),
    "setup/github/1": (
        "Help — Setup GitHub, step 1",
        "Help page · Connect GitHub stepper.",
        "Connect a GitHub App installation or a fine-grained PAT.",
    ),
    "setup/github/2": (
        "Help — Setup GitHub, step 2",
        "Help page · Connect GitHub stepper.",
        "Choose the repositories the factory may work in.",
    ),
    "setup/github/3": (
        "Help — Setup GitHub, step 3",
        "Help page · Connect GitHub stepper.",
        "Link a repository when you create each project.",
    ),
    "setup/llm/1": (
        "Help — Setup LLM providers, step 1",
        "Help page · LLM providers stepper.",
        "Add at least one provider API key — stored write-only in the "
        "vault.",
    ),
    "setup/llm/2": (
        "Help — Setup LLM providers, step 2",
        "Help page · LLM providers stepper.",
        "Route each factory function (PRDs, plans, merges) to a provider.",
    ),
    "setup/llm/3": (
        "Help — Setup LLM providers, step 3",
        "Help page · LLM providers stepper.",
        "Override the routing per project where a different model fits "
        "better.",
    ),
    "setup/workers/1": (
        "Help — Setup workers, step 1",
        "Help page · Workers stepper.",
        "Register a worker and copy its token.",
    ),
    "setup/workers/2": (
        "Help — Setup workers, step 2",
        "Help page · Workers stepper.",
        "Point the runner — or your IDE agent's MCP config — at the "
        "factory with that token.",
    ),
    "setup/workers/3": (
        "Help — Setup workers, step 3",
        "Help page · Workers stepper.",
        "Watch claims, activity, and capabilities on the Workers page.",
    ),
    # US-13.9: the headless MCP worker, documented start to finish — every
    # step below was learned the slow way on 2026-07-20.
    "setup/headless/1": (
        "Help — Setup headless worker, step 1",
        "Help page · Headless worker stepper.",
        "Choose the worker type first. A supervisor runner has a machine "
        "and a controlled shell — it can run test suites and deployments. "
        "A headless CLI worker (claude -p per run) has neither: it authors "
        "PRDs, story splits, plans, and code handed back git-free. Pick by "
        "whether the work needs execution.",
    ),
    "setup/headless/2": (
        "Help — Setup headless worker, step 2",
        "Help page · Headless worker stepper.",
        # US-14.7: the old text claimed the token was one-time and that
        # only a hash is stored. Neither is true — every worker also has
        # a vault_secret_id, and reveal_worker_token() re-reads it under
        # is_own_principal / manage_members. Say what the app does.
        "Register the agent and copy its token. Your worker token stays available: Team → Connect re-reads it whenever you need it, so a lost config file is not a lost worker. It is readable only by the principal it belongs to, or by an org admin with manage_members, and never across orgs. Treat it as a live credential and rotate it if it leaks — rotation, not recovery, is the remedy for exposure.",
    ),
    "setup/headless/3": (
        "Help — Setup headless worker, step 3",
        "Help page · Headless worker stepper.",
        "Save the MCP config Team → Connect generates (your real org, "
        "project and token), and invoke the CLI with -p, --mcp-config, and "
        "--strict-mcp-config so only the factory's tools load.",
    ),
    "setup/headless/4": (
        "Help — Setup headless worker, step 4",
        "Help page · Headless worker stepper.",
        "Allow the whole server in one entry: --allowed-tools "
        "\"mcp__factory\". Naming tools individually is the trap — a "
        "missing entry silently removes that tool with no error naming the "
        "allow-list, so the agent plans blind or loses its findings.",
    ),
    "setup/headless/5": (
        "Help — Setup headless worker, step 5",
        "Help page · Headless worker stepper.",
        "For unattended runs, mint a long-lived credential once with "
        "`claude setup-token` and set CLAUDE_CODE_OAUTH_TOKEN where the "
        "worker runs. An expired interactive session fails the run before "
        "the factory is ever reached.",
    ),
    "setup/headless/6": (
        "Help — Setup headless worker, step 6",
        "Help page · Headless worker stepper.",
        "Prove the connection before dispatching: call list_available_work "
        "over JSON-RPC (Team → Connect has the exact curl). 401 means a "
        "bad or revoked token; an empty pool means no queued work for THIS "
        "project — the pool is per-project, so it can also mean the wrong "
        "project in the URL.",
    ),
    "setup/headless/7": (
        "Help — Setup headless worker, step 7",
        "Help page · Headless worker stepper.",
        "The worker loop, in order: list_available_work → get_instructions "
        "(peek before claiming) → claim_work → get_work_context → do the "
        "work → validate_submission → submit_*. report_progress with a "
        "note extends the lease on long runs and shows the manager the "
        "worker is alive. Hand-back notes ride every submit — no denied "
        "side-channel tool can silence them.",
    ),
    "setup/headless/8": (
        "Help — Setup headless worker, step 8",
        "Help page · Headless worker stepper.",
        "When it fails: 401 = bad/revoked token. Empty pool = nothing "
        "queued, or the wrong project URL. A tool that 'does not exist' = "
        "the allow-list. An auth error before any MCP call = expired agent "
        "auth (see step 5). A lease that expired = the run went back to "
        "the pool; claim it again — nothing is lost.",
    ),
    "setup/servers/1": (
        "Help — Setup servers, step 1",
        "Help page · Deployment servers stepper.",
        "Register a server with its SSH credentials — stored write-only, "
        "never echoed back.",
    ),
    "setup/servers/2": (
        "Help — Setup servers, step 2",
        "Help page · Deployment servers stepper.",
        "Define a deployment per project and environment.",
    ),
    "setup/servers/3": (
        "Help — Setup servers, step 3",
        "Help page · Deployment servers stepper.",
        "Run a deploy — releases land atomically and roll back in one "
        "click.",
    ),
    # --------------------------------------- US-74.6: the topic handbook
    "index/intro": (
        "Help — Index intro",
        "Help index · line under the topic cards.",
        # No count in this sentence on purpose — a number goes stale the
        # first time a topic is added, and nobody looks for it.
        "One short page per topic. Start at the top if Build Mill is new to "
        "you; otherwise jump to the one you need.",
    ),
    "build-order/modes": (
        "Help — Build modes",
        "Help · Build order page, build-mode explainer.",
        "Set on a project under Agent Instructions → Task processing. **By "
        "feature (sequential)** is the default for new projects: a feature is "
        "built as a batch — every story planned and approved before any is "
        "coded — and a later feature waits until the earlier one is done. **By "
        "story (freeform)** routes any story on its own, holding nothing for "
        "its siblings. **By epic** applies the same batching one level up: "
        "every feature documented, then every story planned, then everything "
        "built.",
    ),
    "build-order/concurrency": (
        "Help — Concurrency",
        "Help · Build order page, concurrency switch.",
        "A separate switch on the same card. Build mode decides WHO owns a "
        "build unit; concurrency decides HOW MANY may be in flight. Leave "
        "concurrency on (the default) and independent work overlaps. Tick "
        "**Sequential** to hold every other story — plan and code — until the "
        "one in flight reaches merged; use it when stories collide on the same "
        "files often enough that merge conflicts cost more than the waiting.",
    ),
    "build-order/holds": (
        "Help — Why an item is held",
        "Help · Build order page, the hourglass.",
        "An hourglass on Things to Do means the build rules will not let that "
        "item start yet, and its button stays disabled until they do. The "
        "reason names the blocker — an earlier feature that has not finished, "
        "sibling stories whose plans you have not approved yet, a story ahead "
        "of this one still running, or siblings still being curated. Nothing "
        "is stuck: clear the named blocker and the hourglass goes away on the "
        "next refresh. Approvals are never held — you can always clear a gate.",
    ),
    "build-order/where": (
        "Help — Where holds appear",
        "Help · Build order page, which surfaces show holds.",
        "Things to Do shows work waiting on you, with the hourglass on what "
        "cannot move. Factory Queue shows runs already dispatched and which of "
        "them the pool is holding. Both read the same rules the factory "
        "enforces, so neither can show you something the dispatch would refuse.",
    ),
    "github/connection": (
        "Help — GitHub connection",
        "Help · GitHub page, the App connection.",
        "Connect a GitHub App installation (or a fine-grained token) once under "
        "Settings → GitHub, choose which repositories the factory may touch, "
        "then link a repository to each project. The factory reads and writes "
        "code only in repositories you listed there.",
    ),
    "github/remote": (
        "Help — The factory git remote",
        "Help · GitHub page, the factory's own remote.",
        "Build Mill serves its own git remote. Workers clone and push through "
        "it using their worker token as the password — no GitHub credentials "
        "ever reach a worker, and access can be revoked by revoking one token. "
        "The factory then opens the pull request itself.",
    ),
    "github/transports": (
        "Help — Worker transports",
        "Help · GitHub page, MCP vs git-native workers.",
        "An AI worker normally needs no git at all: it downloads the working "
        "tree as a zip pinned to a base commit and hands the changed files "
        "back, and the factory builds the commit and opens the PR. A person in "
        "an IDE (or a repository too large to snapshot) clones and pushes "
        "through the factory remote instead. Both land in the same review.",
    ),
    "github/prs": (
        "Help — Branches and pull requests",
        "Help · GitHub page, branching and PRs.",
        "The branch a run works on is named in its run context; a code run "
        "never creates others. How branches map to work follows the project's "
        "development strategy — per story, per work item, or straight to main. "
        "Approving a submission merges the pull request; sending it back "
        "returns it with your comment, which the retry run carries.",
    ),
    "releases/what": (
        "Help — What a release is",
        "Help · Releases page, the pinned build.",
        "One build, cut from the default branch and pinned to a commit. "
        "Everything downstream — the notes, the UAT deploy, the promotion — "
        "uses that pinned commit, never whatever the branch head is later. A "
        "release is immutable: if UAT fails you reject it and cut a new one, "
        "so a version name always means exactly one build.",
    ),
    "releases/cut": (
        "Help — Cutting a release",
        "Help · Releases page, the cut step.",
        "Cutting pins the commit, snapshots the work items merged since the "
        "last released version, and tags it. The factory computes the version; "
        "you may override the proposal at this moment and never again.",
    ),
    "releases/uat": (
        "Help — Release UAT",
        "Help · Releases page, the UAT step.",
        "Every release goes to UAT first — it is not a choice. An agent writes "
        "the notes from the real commit range, deploys the pinned commit to the "
        "project's UAT deployment, and health-checks it.",
    ),
    "releases/signoff": (
        "Help — Test and sign-off",
        "Help · Releases page, running the test cases.",
        "The release carries the included work items' test cases plus "
        "regression cases the agent wrote. A human runs them: each case is one "
        "line with Pass / Fail / Blocked, and opens for its steps and expected "
        "result. **Pass all** records every case you have not judged yet and "
        "never overwrites a Fail or Blocked you already entered. Sign-off "
        "needs the UAT deploy to have succeeded AND every case to have passed — "
        "blocked counts as not passed.",
    ),
    "releases/promote": (
        "Help — Promote and roll back",
        "Help · Releases page, promotion to production.",
        "Promotion ships the same pinned build to production. It never "
        "re-versions and is never automatic. Deployments roll back in one click.",
    ),
    "workers/roster": (
        "Help — One roster",
        "Help · Team page, people and agents together.",
        "People and agents are the same kind of thing here: both appear on "
        "Team, both have a name, a role and a profile, and both claim work from "
        "the same pool, first come first served. What differs is how they "
        "connect and what they are allowed to do.",
    ),
    "workers/capabilities": (
        "Help — What an agent may do",
        "Help · Team page, roles, run kinds and leases.",
        "A role carries capabilities, and an agent additionally carries the run "
        "kinds it may take (PRD, plan, code, review, and so on). A run sits in "
        "the pool until an authorized worker claims it, holds a lease while "
        "working, and extends that lease as it reports progress. If a lease "
        "expires the run returns to the pool — nothing is lost.",
    ),
    "workers/machines": (
        "Help — Machines",
        "Help · Team page, machines vs headless workers.",
        "A machine is a box the factory reaches over SSH: a deploy target, a "
        "host for coding agents, or both. An agent on a machine has a "
        "controlled shell, so it can run test suites and deployments. A "
        "headless CLI worker has no machine and no shell — it authors PRDs, "
        "story splits, plans, and code handed back git-free. Pick by whether "
        "the work needs to execute anything.",
    ),
    "guidelines/project": (
        "Help — Agent Instructions",
        "Help · Guidelines page, the Agent Instructions document.",
        "Per project, on the Agent Instructions tab: one markdown document — how "
        "this codebase is built, what conventions hold, what an agent must not "
        "do, and how releases are versioned. It is the body of the AGENTS.md the "
        "factory publishes to the repository, and a new project starts with its "
        "template's copy. Mark it ready when it is worth handing to a worker; "
        "edit later and the page nudges you that it has changed since.",
    ),
    "guidelines/instructions": (
        "Help — Task Instructions",
        "Help · Guidelines page, per-run-kind instruction files.",
        "Per project, on Task Instructions: one instruction file per run kind "
        "(published as .buildmill/<File>.md and indexed from AGENTS.md), so a "
        "planning run and a code run can be told different things. The same tab "
        "carries Task processing — build mode, concurrency, and the auto-approve "
        "switches.",
    ),
    "guidelines/learnings": (
        "Help — Learnings",
        "Help · Guidelines page, agent-proposed changes.",
        "When a run discovers something worth keeping, it can propose a revised "
        "Agent Instructions document, and a refresh run studies the repository "
        "and proposes revised instruction files. Proposals wait for you on Things "
        "to Do as a diff per file; accepting one edits the factory's text "
        "(publish when ready), so the next run starts where the last one left "
        "off.",
    ),
    "guidelines/context": (
        "Help — What a run receives",
        "Help · Guidelines page, the run context bundle.",
        "Every run is handed the story and its acceptance criteria, the "
        "governing PRD, the approved plan (on code runs), the Agent Instructions, "
        "the "
        "project's learnings, attached documents, and — on a retry — the "
        "feedback from the rejection. Each work item also carries a comment "
        "thread you and the worker share.",
    ),
}


def help_catalog() -> list[dict]:
    """Catalog entries for the admin template library (group "help")."""
    return [
        {
            "key": f"help/{suffix}",
            "group": "help",
            "label": label,
            "description": description,
            "variables": [],
            "default": default,
        }
        for suffix, (label, description, default) in _ENTRIES.items()
    ]
