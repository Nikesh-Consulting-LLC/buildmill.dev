import type { LucideIcon } from "lucide-react";
import {
  Bot,
  Cloud,
  Compass,
  Database,
  FileText,
  FlaskConical,
  GitBranch,
  Globe,
  KeyRound,
  Rocket,
  Server,
  Hammer,
  ClipboardList,
  Layers,
  Workflow,
} from "lucide-react";
import type { StageActor } from "@/lib/stage-tracker";
import type { IssueStatus } from "@/components/status-badge";

/** US-2.30: structure is code, words are data. Everything in this file is
 * structural — sections, stages, nodes, steps, links — except
 * HELP_DEFAULTS, the factory-default text mirror of the API's
 * help_content.py (the RPC-failure fallback; keep the two in sync). The
 * effective text comes from useHelpText(): superadmin override, else these
 * defaults. */

/** US-74.6: the handbook is a set of topic pages, not one scroll.
 *
 * A section is still the unit of content (and still the anchor id, so old
 * `/help#pipeline` links keep resolving — see SECTION_TOPIC below). A topic is
 * a page that holds a few related sections. Adding a section means adding it
 * to exactly one topic; nothing else needs to know. */
export type HelpSection = {
  id: string;
  label: string;
  /** Setup guides rendered inside this section, in this order. Omitted
   * entirely for sections whose body is a diagram or prose. */
  guides?: SetupGuideKey[];
};

export type HelpTopic = {
  slug: string;
  title: string;
  /** One line on the index card — what you'd come to this page to find out. */
  blurb: string;
  icon: LucideIcon;
  sections: HelpSection[];
};

export const HELP_TOPICS: HelpTopic[] = [
  {
    slug: "getting-started",
    title: "Getting started",
    blurb:
      "Your first project end to end: connect a repository, define work, dispatch it, review it, ship it.",
    icon: Compass,
    sections: [
      { id: "overview", label: "The big picture" },
      { id: "setup", label: "Set up a project", guides: ["project", "llm"] },
    ],
  },
  {
    slug: "work-items",
    title: "Work items & the pipeline",
    blurb:
      "Features, stories, bugs and chores — how each type is routed, and the gates it walks through on its way to merged.",
    icon: ClipboardList,
    sections: [
      { id: "pipeline", label: "The pipeline" },
      // us-96.5 rework: since Phase 96 the TYPE decides the path — one
      // lane per type, drawn, before the single-rail lifecycle chart.
      { id: "routing", label: "How each type is routed" },
      { id: "lifecycle", label: "The full lifecycle" },
      { id: "statuses", label: "Statuses & colors" },
    ],
  },
  {
    slug: "build-order",
    title: "Build order & the serial law",
    blurb:
      "Which work may start when — the two routing switches, one item at a time, and why an item shows an hourglass.",
    icon: Workflow,
    sections: [{ id: "build-order", label: "Build order & the serial law" }],
  },
  {
    slug: "github",
    title: "GitHub",
    blurb:
      "The App connection, the factory's own git remote, and how a worker's changes become a pull request.",
    icon: GitBranch,
    sections: [{ id: "github", label: "GitHub & the git remote", guides: ["github"] }],
  },
  {
    slug: "releases",
    title: "Releases & deployments",
    blurb:
      "Cut, UAT, test, sign off, promote — one pinned build the whole way, and how to roll it back.",
    icon: Rocket,
    sections: [
      { id: "releases", label: "Releases & deployments", guides: ["servers"] },
    ],
  },
  {
    slug: "team",
    title: "Team & workers",
    blurb:
      "People and agents share one roster. How each connects, and what a machine is for.",
    icon: Bot,
    sections: [
      // US-35.4: the four words the app uses for its own objects, defined once
      // and only here. A glossary repeated on every surface is one that drifts.
      { id: "glossary", label: "What things are called" },
      {
        id: "workers",
        label: "Agents & people",
        guides: ["workers", "headless"],
      },
    ],
  },
  {
    slug: "guidelines",
    title: "Guidelines & agent instructions",
    blurb:
      "What every run is told before it starts, where you edit it, and how learnings feed back in.",
    icon: FileText,
    sections: [{ id: "guidelines", label: "Guidelines & agent instructions" }],
  },
  {
    slug: "architecture",
    title: "How it's built",
    blurb:
      "The components, the two trust zones, and where your credentials actually live.",
    icon: Layers,
    sections: [{ id: "architecture", label: "How it's built" }],
  },
];

/** Every section, flat and in reading order. */
export const HELP_SECTIONS: readonly HelpSection[] = HELP_TOPICS.flatMap(
  (t) => t.sections
);

/** Section id → the topic page that now holds it. US-74.6 keeps every
 * `/help#<section>` link that worked before working after: the index reads the
 * fragment and forwards to the owning page. */
export const SECTION_TOPIC: Record<string, string> = Object.fromEntries(
  HELP_TOPICS.flatMap((t) => t.sections.map((s) => [s.id, t.slug]))
);

/** Retired section ids that used to be anchors on the one-page handbook, so
 * a bookmark from before the split still lands somewhere sensible. */
export const LEGACY_SECTION_TOPIC: Record<string, string> = {
  // The old page had a single "setup" anchor covering all six guides; the
  // guides now live with the thing they set up, and Getting started is the
  // one that reads as "start here".
  "setup-guides": "getting-started",
};

// ------------------------------------------------ topic prose (US-74.6)

/** A headed paragraph inside a topic section. Structure here, words in
 * HELP_DEFAULTS / the override table, like everything else on this page. */
export type HelpPoint = { heading: string; textKey: string };

export const HELP_POINTS: Record<string, HelpPoint[]> = {
  "build-order": [
    { heading: "The two switches", textKey: "help/build-order/modes" },
    { heading: "One item at a time", textKey: "help/build-order/concurrency" },
    { heading: "Why an item shows an hourglass", textKey: "help/build-order/holds" },
    { heading: "Where to look", textKey: "help/build-order/where" },
  ],
  github: [
    { heading: "The connection", textKey: "help/github/connection" },
    { heading: "The factory's git remote", textKey: "help/github/remote" },
    { heading: "Two kinds of worker", textKey: "help/github/transports" },
    { heading: "Branches and pull requests", textKey: "help/github/prs" },
  ],
  releases: [
    { heading: "What a release is", textKey: "help/releases/what" },
    { heading: "Cut", textKey: "help/releases/cut" },
    { heading: "UAT", textKey: "help/releases/uat" },
    { heading: "Test and sign-off", textKey: "help/releases/signoff" },
    { heading: "Promote and roll back", textKey: "help/releases/promote" },
  ],
  workers: [
    { heading: "One roster", textKey: "help/workers/roster" },
    { heading: "What an agent may do", textKey: "help/workers/capabilities" },
    { heading: "Machines", textKey: "help/workers/machines" },
  ],
  guidelines: [
    { heading: "Project guidelines", textKey: "help/guidelines/project" },
    { heading: "Agent instructions", textKey: "help/guidelines/instructions" },
    { heading: "Learnings", textKey: "help/guidelines/learnings" },
    { heading: "What a run receives", textKey: "help/guidelines/context" },
  ],
};

// ------------------------------------------------------------- hero flow

export type HeroStep = {
  label: string;
  icon: LucideIcon;
  tone: "person" | "agent" | "shipped";
};

export const HERO_STEPS: HeroStep[] = [
  { label: "Define", icon: FileText, tone: "person" },
  { label: "Document", icon: ClipboardList, tone: "agent" },
  { label: "Build", icon: Hammer, tone: "agent" },
  { label: "Test", icon: FlaskConical, tone: "agent" },
  { label: "Deploy", icon: Rocket, tone: "shipped" },
];

// ---------------------------------------------------- pipeline walkthrough

export type HelpStage = {
  key: "draft" | "plan" | "build" | "uat" | "release";
  label: string;
  actor: StageActor;
  /** Overrides the generic "You act" / "The factory acts" chip when a
   * stage is genuinely both (you trigger, Build Mill executes). */
  actorLabel?: string;
  href: string;
  linkLabel: string;
};

export const PIPELINE_STAGES: HelpStage[] = [
  {
    key: "draft",
    label: "Draft",
    actor: "person",
    href: "/issues",
    linkLabel: "Work Items",
  },
  {
    key: "plan",
    label: "Plan",
    actor: "agent",
    href: "/workbench",
    linkLabel: "Things to Do",
  },
  {
    key: "build",
    label: "Build",
    actor: "agent",
    href: "/workers",
    linkLabel: "Workers",
  },
  {
    key: "uat",
    label: "UAT",
    actor: "person",
    href: "/tests",
    linkLabel: "Tests",
  },
  {
    key: "release",
    label: "Release",
    actor: "person",
    actorLabel: "You trigger · Build Mill releases",
    href: "/projects",
    linkLabel: "Projects",
  },
];

// -------------------------------------------------------- architecture map

export type HelpNode = {
  key: "web" | "api" | "supabase" | "workers" | "github";
  label: string;
  sublabel: string;
  icon: LucideIcon;
  zone: "cloud" | "operator";
};

export const ARCHITECTURE_NODES: HelpNode[] = [
  { key: "web", label: "Web app", sublabel: "Next.js", icon: Globe, zone: "cloud" },
  { key: "supabase", label: "Supabase", sublabel: "auth · data · realtime", icon: Database, zone: "cloud" },
  { key: "api", label: "API", sublabel: "FastAPI orchestrator", icon: Cloud, zone: "cloud" },
  { key: "github", label: "GitHub", sublabel: "source of truth", icon: GitBranch, zone: "cloud" },
  { key: "workers", label: "Workers", sublabel: "runner · IDE agents", icon: Bot, zone: "operator" },
];

// ------------------------------------------------------------ status legend

export const ALL_STATUSES: IssueStatus[] = [
  "draft",
  "prd-review",
  "ready",
  "planning",
  "plan-review",
  "planned",
  "queued",
  "running",
  "needs-fixes",
  "in-review",
  "merged",
  "done",
  "succeeded",
  "cancelled",
  "failed",
];

// ------------------------------------------------------------ setup guides

export type SetupGuideKey =
  | "github"
  | "llm"
  | "workers"
  | "headless"
  | "servers"
  | "project";

export type SetupGuide = {
  key: SetupGuideKey;
  title: string;
  icon: LucideIcon;
  steps: { textKey: string; href: string; linkLabel: string }[];
};

export const SETUP_GUIDES: SetupGuide[] = [
  {
    // us-7.12: mirrors the six-check project-setup readiness (us-7.7), in
    // order — a project marked Ready is one an agent has everything to build.
    key: "project",
    title: "Set up a project to run the factory",
    icon: Layers,
    steps: [
      { textKey: "help/setup/project/1", href: "/projects", linkLabel: "Projects" },
      { textKey: "help/setup/project/2", href: "/projects", linkLabel: "Projects" },
      { textKey: "help/setup/project/3", href: "/projects", linkLabel: "Projects" },
      { textKey: "help/setup/project/4", href: "/projects", linkLabel: "Projects" },
      { textKey: "help/setup/project/5", href: "/projects", linkLabel: "Projects" },
      { textKey: "help/setup/project/6", href: "/projects", linkLabel: "Projects" },
    ],
  },
  {
    key: "github",
    title: "Connect GitHub",
    icon: GitBranch,
    steps: [
      {
        textKey: "help/setup/github/1",
        href: "/settings/github",
        linkLabel: "GitHub settings",
      },
      {
        textKey: "help/setup/github/2",
        href: "/settings/github",
        linkLabel: "GitHub settings",
      },
      {
        textKey: "help/setup/github/3",
        href: "/projects",
        linkLabel: "Projects",
      },
    ],
  },
  {
    key: "llm",
    title: "LLM providers",
    icon: KeyRound,
    steps: [
      {
        textKey: "help/setup/llm/1",
        href: "/settings/llm-providers",
        linkLabel: "LLM Providers",
      },
      {
        textKey: "help/setup/llm/2",
        href: "/settings/llm-providers",
        linkLabel: "LLM Providers",
      },
      {
        textKey: "help/setup/llm/3",
        href: "/projects",
        linkLabel: "Projects",
      },
    ],
  },
  {
    key: "workers",
    title: "Workers",
    icon: Bot,
    steps: [
      {
        textKey: "help/setup/workers/1",
        href: "/settings/workers",
        linkLabel: "Worker settings",
      },
      {
        textKey: "help/setup/workers/2",
        href: "/workers",
        linkLabel: "Workers",
      },
      {
        textKey: "help/setup/workers/3",
        href: "/workers",
        linkLabel: "Workers",
      },
    ],
  },
  {
    // US-13.9: from nothing to a claimed run, without reverse-engineering
    // the CLI or discovering a required flag by watching a run fail.
    key: "headless",
    title: "Headless CLI worker",
    icon: Bot,
    steps: [
      {
        textKey: "help/setup/headless/1",
        href: "/team?tab=connect",
        linkLabel: "Team → Connect",
      },
      {
        textKey: "help/setup/headless/2",
        href: "/team",
        linkLabel: "Team",
      },
      {
        textKey: "help/setup/headless/3",
        href: "/team?tab=connect",
        linkLabel: "Team → Connect",
      },
      {
        textKey: "help/setup/headless/4",
        href: "/team?tab=connect",
        linkLabel: "Team → Connect",
      },
      {
        textKey: "help/setup/headless/5",
        href: "/team?tab=connect",
        linkLabel: "Team → Connect",
      },
      {
        textKey: "help/setup/headless/6",
        href: "/team?tab=connect",
        linkLabel: "Team → Connect",
      },
      {
        textKey: "help/setup/headless/7",
        href: "/team?tab=live",
        linkLabel: "Team → Live",
      },
      {
        textKey: "help/setup/headless/8",
        href: "/team?tab=live",
        linkLabel: "Team → Live",
      },
    ],
  },
  {
    key: "servers",
    title: "Deployment servers",
    icon: Server,
    steps: [
      {
        textKey: "help/setup/servers/1",
        href: "/servers",
        linkLabel: "Servers",
      },
      {
        textKey: "help/setup/servers/2",
        href: "/projects",
        linkLabel: "Projects",
      },
      {
        textKey: "help/setup/servers/3",
        href: "/projects",
        linkLabel: "Projects",
      },
    ],
  },
];

// ------------------------------------------- factory-default text (mirror)

export const HELP_DEFAULTS: Record<string, string> = {
  "help/overview/intro":
    "Build Mill turns a written story into a merged pull request. You " +
    "define the work and make the calls; the factory plans, codes, and " +
    "ships between your decisions.",
  "help/pipeline/intro":
    "This is the story's rail — the shape the other types vary from " +
    "(see “How each type is routed” below: a chore skips planning, a bug's " +
    "plan is a root cause analysis, a feature routes its stories as one). " +
    "Work items live inside an epic — the numbering root: one epic is " +
    "active at a time, new items land in it, and you close it and start " +
    "the next only once every item is completed, deployed, abandoned, or " +
    "deleted. Each item carries a readable, epic-scoped id (FEAT-1.4, " +
    "US-1.4.1, BUG-1.5). Requirements live in Build Mill; GitHub Issue " +
    "sync is retired. Click a stage to see what happens there.",
  "help/pipeline/draft":
    "You write the story: what to build, and acceptance criteria that " +
    "make “done” testable. Attach documents worth reading. " +
    "Dispatch planning when the story is ready — or, for a story inside a " +
    "feature, dispatch the feature and it plans them all.",
  "help/pipeline/plan":
    "The agent studies the repository and writes an implementation plan " +
    "and a test plan — no code yet. Both land for your review; " +
    "approving them unlocks the build. On a bug this stage is the root " +
    "cause analysis (what broke, why, the proposed fix — in words) and " +
    "approving it unlocks the fix. A chore has no plan stage at all.",
  "help/pipeline/build":
    "The agent implements the approved plan and pushes through the " +
    "factory's git remote. How it branches follows the project's " +
    "development strategy — a branch per story, per work item, or straight " +
    "to main. PR modes wait for your review; a main-strategy project " +
    "commits directly and bypasses the PR gate.",
  "help/pipeline/uat":
    "The merged change deploys to UAT from the project's UAT release " +
    "branch. You open the environment at its Website, try it against the " +
    "story, and record QA sign-off — or send it back.",
  "help/pipeline/release":
    "You approve promotion and trigger the Production deploy from the " +
    "Production release branch. Cutting a release mints a system-computed " +
    "version V<epic>.<release-seq> and git-tags it; promotion reuses the " +
    "same version. Releases are tracked per environment with one-click " +
    "rollback, and QA sign-offs and promotions show in the activity feed.",
  "help/lifecycle/intro":
    "Every path a work item can take — the happy path down the middle, " +
    "and every approval, rejection, retry, and cancel around it. Amber " +
    "is you; blue is the factory; green is shipped code.",
  "help/architecture/intro":
    "Two trust zones: the cloud coordinates, while code checkouts and " +
    "provider credentials stay on the operator's side. Click a " +
    "component to see what it does.",
  "help/architecture/web":
    "The app you're using now. It talks straight to Supabase for data " +
    "under row-level security, and to the API only for orchestration.",
  "help/architecture/api":
    "Thin FastAPI orchestrator: dispatch, worker callbacks, GitHub " +
    "operations, LLM calls, and the factory git remote workers push " +
    "through. The only component that can read stored server " +
    "credentials.",
  "help/architecture/supabase":
    "System of record: auth, Postgres, storage, realtime. Every table " +
    "is org-scoped under row-level security; live status updates flow " +
    "from here.",
  "help/architecture/workers":
    "Anything that claims work and hands it back: the autonomous " +
    "runner, or a person driving an IDE agent over MCP. Workers clone " +
    "and push via the factory's git remote — no GitHub credentials on " +
    "any worker.",
  "help/architecture/github":
    "Source of truth for code. Branches, PRs, and CI live there; the " +
    "factory stores links and mirrored status, never copies of code.",
  "help/statuses/intro": "Every badge in the app, decoded.",
  "help/status/draft": "Being written; nothing dispatched yet.",
  "help/status/prd-review": "A drafted PRD awaits your approval.",
  "help/status/ready": "PRD approved; ready to break into stories.",
  "help/status/planning":
    "The agent is writing the implementation and test plans.",
  "help/status/plan-review": "The plans await your review.",
  "help/status/planned": "Plan approved; ready to dispatch the code run.",
  "help/status/queued": "Waiting for a worker to claim it.",
  "help/status/running": "A worker is on it right now.",
  "help/status/needs-fixes":
    "Rejected in review; dispatch a fix run carrying your feedback.",
  "help/status/in-review": "The diff is ready and waiting on your review.",
  "help/status/merged": "Approved and merged to the default branch.",
  "help/status/done": "Released and closed out.",
  "help/status/succeeded": "The run finished cleanly.",
  "help/status/cancelled": "Stopped on request before finishing.",
  "help/status/failed": "The run errored; fix the cause and re-dispatch.",
  "help/stage-state/complete": "This stage is finished.",
  "help/stage-state/in-progress":
    "The factory is working; nothing needed from you.",
  "help/stage-state/waiting":
    "Paused on you — review, sign off, or dispatch.",
  "help/stage-state/failed":
    "The stage's run errored; re-dispatch to retry.",
  "help/stage-state/not-started": "Not reached yet.",
  "help/stage-state/not-tracked":
    "No deployment is classified for this environment, so the stage " +
    "can't be tracked.",
  "help/setup/intro":
    "First get a project ready to run the factory, then the connections " +
    "behind it. Each step links to the page where it happens.",
  "help/setup/project/1":
    "Write a Project Summary on the Overview tab — what the project is " +
    "and its goals. Then fill in the Guidelines tab and, on Worker " +
    "Instructions, the per-stage instructions agents are given.",
  "help/setup/project/2":
    "Connect the repository and pick the UAT and Production release " +
    "branches (create them from main without leaving Build Mill), plus a " +
    "development branching strategy.",
  "help/setup/project/3":
    "Review the guidelines — including the Versioning & Release section — " +
    "and mark them ready. Edit later and an “edited since ready” " +
    "nudge appears.",
  "help/setup/project/4":
    "Review the worker instructions per run kind and mark them ready too.",
  "help/setup/project/5":
    "Define the build & test config an agent runs against — the runtime " +
    "and setup, the build/test/lint commands, and any write-only " +
    "sandbox/test config values it needs to verify its work.",
  "help/setup/project/6":
    "Define a UAT and a Production deployment, each with a reachable " +
    "Website. The Overview readiness checklist then reads six-for-six — " +
    "Ready means an agent has everything it needs to build.",
  "help/setup/github/1":
    "Connect a GitHub App installation or a fine-grained PAT.",
  "help/setup/github/2":
    "Choose the repositories the factory may work in.",
  "help/setup/github/3":
    "Link a repository when you create each project.",
  "help/setup/llm/1":
    "Add at least one provider API key — stored write-only in the " +
    "vault.",
  "help/setup/llm/2":
    "Route each factory function (PRDs, plans, merges) to a provider.",
  "help/setup/llm/3":
    "Override the routing per project where a different model fits " +
    "better.",
  "help/setup/workers/1": "Register a worker and copy its token.",
  "help/setup/workers/2":
    "Point the runner — or your IDE agent's MCP config — at the " +
    "factory with that token.",
  "help/setup/workers/3":
    "Watch claims, activity, and capabilities on the Workers page.",
  "help/setup/headless/1":
    "Choose the worker type first. A supervisor runner has a machine " +
    "and a controlled shell — it can run test suites and deployments. " +
    "A headless CLI worker (claude -p per run) has neither: it authors " +
    "PRDs, story splits, plans, and code handed back git-free. Pick by " +
    "whether the work needs execution.",
  // US-14.7: this used to promise a one-time reveal and hash-only
  // storage. Every worker also has a vault_secret_id, and
  // reveal_worker_token() re-reads it under is_own_principal /
  // manage_members — so the app said the opposite of what it does.
  "help/setup/headless/2":
    "Register the agent and copy its token. Your worker token stays " +
    "available: Team → Connect re-reads it whenever you need it, so a " +
    "lost config file is not a lost worker. It is readable only by the " +
    "principal it belongs to, or by an org admin with manage_members, " +
    "and never across orgs. Treat it as a live credential and rotate it " +
    "if it leaks — rotation, not recovery, is the remedy for exposure.",
  "help/setup/headless/3":
    "Save the MCP config Team → Connect generates (your real org, " +
    "project and token), and invoke the CLI with -p, --mcp-config, and " +
    "--strict-mcp-config so only the factory's tools load.",
  "help/setup/headless/4":
    "Allow the whole server in one entry: --allowed-tools " +
    '"mcp__factory". Naming tools individually is the trap — a ' +
    "missing entry silently removes that tool with no error naming the " +
    "allow-list, so the agent plans blind or loses its findings.",
  "help/setup/headless/5":
    "For unattended runs, mint a long-lived credential once with " +
    "`claude setup-token` and set CLAUDE_CODE_OAUTH_TOKEN where the " +
    "worker runs. An expired interactive session fails the run before " +
    "the factory is ever reached.",
  "help/setup/headless/6":
    "Prove the connection before dispatching: call list_available_work " +
    "over JSON-RPC (Team → Connect has the exact curl). 401 means a " +
    "bad or revoked token; an empty pool means no queued work for THIS " +
    "project — the pool is per-project, so it can also mean the wrong " +
    "project in the URL.",
  "help/setup/headless/7":
    "The worker loop, in order: list_available_work → get_instructions " +
    "(peek before claiming) → claim_work → get_work_context → do the " +
    "work → validate_submission → submit_*. report_progress with a " +
    "note extends the lease on long runs and shows the manager the " +
    "worker is alive. Hand-back notes ride every submit — no denied " +
    "side-channel tool can silence them.",
  "help/setup/headless/8":
    "When it fails: 401 = bad/revoked token. Empty pool = nothing " +
    "queued, or the wrong project URL. A tool that 'does not exist' = " +
    "the allow-list. An auth error before any MCP call = expired agent " +
    "auth (see step 5). A lease that expired = the run went back to " +
    "the pool; claim it again — nothing is lost.",
  "help/setup/servers/1":
    "Register a server with its SSH credentials — stored write-only, " +
    "never echoed back.",
  "help/setup/servers/2":
    "Define a deployment per project and environment.",
  "help/setup/servers/3":
    "Run a deploy — releases land atomically and roll back in one " +
    "click.",

  // --------------------------------------------- US-74.6 topic handbook
  // No count in this sentence on purpose — a number here goes stale the first
  // time a topic is added, and nobody thinks to look for it.
  "help/index/intro":
    "One short page per topic. Start at the top if Build Mill is new to " +
    "you; otherwise jump to the one you need.",
  "help/build-order/modes":
    "Two checkboxes on a project under Worker instructions → Task " +
    "processing, both on by default. **Follow build order** decides the " +
    "ORDER: the queue drains Epic → Feature → Story; off, it drains in the " +
    "order you dispatched. **Route feature as one** decides the UNIT: a " +
    "feature's stories are planned as a batch and built as one " +
    "feature-owned run and PR, and pressing Plan or Code on a healthy " +
    "child is refused with the feature named (“FEAT-2.3 owns the plan — " +
    "dispatch the feature to plan all 5 stories”). A story in trouble " +
    "(failed / needs-fixes) or one being re-planned still routes on its " +
    "own — a stuck story must never wedge its batch. Off, every story is " +
    "its own unit. Standalone stories, bugs and chores are always their " +
    "own unit whatever the switches say.",
  "help/build-order/concurrency":
    "There is no concurrency knob — one law with no checkbox: a project " +
    "works ONE routing unit at a time, from its first claimed run until " +
    "its work merges. A story (or a whole feature, when the switch groups " +
    "them) owns the project while it is being planned, awaiting your plan " +
    "approval, holding an approved plan, being built, or sitting unmerged. " +
    "Everything queued behind it is held — never refused. Queueing is " +
    "always legal; starting is what waits.",
  "help/build-order/holds":
    "An hourglass on Things to Do or a Held pill in the Factory Queue means " +
    "the rules will not let that item start yet. The reason names the " +
    "blocker in a sentence — “US-2.3.2 is awaiting your plan approval”, " +
    "“FEAT-2.1 · Login is ahead in the queue”, “2 sibling stories still " +
    "being curated”. Nothing is stuck: clear the named blocker and it " +
    "moves on the next refresh. Approvals are never held — you can always " +
    "clear a gate. A hard refusal (the feature owns this phase) shows on " +
    "the button itself and points at the feature page.",
  "help/build-order/where":
    "Things to Do shows work waiting on you — a feature with children as " +
    "ONE row, its batch position in a line and any troubled child named on " +
    "the row. Factory Queue shows runs already dispatched, a feature's " +
    "runs collapsed into one unit you drag and pause as a block. Both read " +
    "the same rules the factory enforces, so neither can show you " +
    "something the dispatch would refuse.",
  "help/github/connection":
    "Connect a GitHub App installation (or a fine-grained token) once under " +
    "Settings → GitHub, choose which repositories the factory may touch, " +
    "then link a repository to each project. The factory reads and writes " +
    "code only in repositories you listed there.",
  "help/github/remote":
    "Build Mill serves its own git remote. Workers clone and push through " +
    "it using their worker token as the password — no GitHub credentials " +
    "ever reach a worker, and access can be revoked by revoking one token. " +
    "The factory then opens the pull request itself.",
  "help/github/transports":
    "An AI worker normally needs no git at all: it downloads the working " +
    "tree as a zip pinned to a base commit and hands the changed files " +
    "back, and the factory builds the commit and opens the PR. A person in " +
    "an IDE (or a repository too large to snapshot) clones and pushes " +
    "through the factory remote instead. Both land in the same review.",
  "help/github/prs":
    "The branch a run works on is named in its run context; a code run " +
    "never creates others. How branches map to work follows the project's " +
    "development strategy — per story, per work item, or straight to main. " +
    "Approving a submission merges the pull request; sending it back " +
    "returns it with your comment, which the retry run carries.",
  "help/releases/what":
    "One build, cut from the default branch and pinned to a commit. " +
    "Everything downstream — the notes, the UAT deploy, the promotion — " +
    "uses that pinned commit, never whatever the branch head is later. A " +
    "release is immutable: if UAT fails you reject it and cut a new one, " +
    "so a version name always means exactly one build.",
  "help/releases/cut":
    "Cutting pins the commit, snapshots the work items merged since the " +
    "last released version, and tags it. The factory computes the version; " +
    "you may override the proposal at this moment and never again.",
  "help/releases/uat":
    "Every release goes to UAT first — it is not a choice. An agent writes " +
    "the notes from the real commit range, deploys the pinned commit to the " +
    "project's UAT deployment, and health-checks it.",
  "help/releases/signoff":
    "The release carries the included work items' test cases plus " +
    "regression cases the agent wrote. A human runs them: each case is one " +
    "line with Pass / Fail / Blocked, and opens for its steps and expected " +
    "result. **Pass all** records every case you have not judged yet and " +
    "never overwrites a Fail or Blocked you already entered. Sign-off " +
    "needs the UAT deploy to have succeeded AND every case to have passed — " +
    "blocked counts as not passed.",
  "help/releases/promote":
    "Promotion ships the same pinned build to production. It never " +
    "re-versions and is never automatic. Deployments roll back in one click.",
  "help/workers/roster":
    "People and agents are the same kind of thing here: both appear on " +
    "Team, both have a name, a role and a profile, and both claim work from " +
    "the same pool, first come first served. What differs is how they " +
    "connect and what they are allowed to do.",
  "help/workers/capabilities":
    "A role carries capabilities, and an agent additionally carries the run " +
    "kinds it may take (PRD, plan, code, review, and so on). A run sits in " +
    "the pool until an authorized worker claims it, holds a lease while " +
    "working, and extends that lease as it reports progress. If a lease " +
    "expires the run returns to the pool — nothing is lost.",
  "help/workers/machines":
    "A machine is a box the factory reaches over SSH: a deploy target, a " +
    "host for coding agents, or both. An agent on a machine has a " +
    "controlled shell, so it can run test suites and deployments. A " +
    "headless CLI worker has no machine and no shell — it authors PRDs, " +
    "story splits, plans, and code handed back git-free. Pick by whether " +
    "the work needs to execute anything.",
  "help/guidelines/project":
    "Per project, on the Guidelines tab: how this codebase is built, what " +
    "conventions hold, what an agent must not do. Mark them ready when " +
    "they are worth handing to a worker; edit later and the page nudges " +
    "you that they have changed since.",
  "help/guidelines/instructions":
    "Per project, on Agent Instructions: one instruction set per run kind, " +
    "so a planning run and a code run can be told different things. The " +
    "same tab carries Task processing — build mode, concurrency, and the " +
    "auto-approve switches.",
  "help/guidelines/learnings":
    "When a run discovers something worth keeping, it can propose a change " +
    "to the guidelines. Proposals wait for you on Things to Do; approving " +
    "one edits the guidelines, so the next run starts where the last one " +
    "left off.",
  "help/guidelines/context":
    "Every run is handed the story and its acceptance criteria, the " +
    "governing PRD, the approved plan (on code runs), these guidelines, the " +
    "project's learnings, attached documents, and — on a retry — the " +
    "feedback from the rejection. Each work item also carries a comment " +
    "thread you and the worker share.",
};
