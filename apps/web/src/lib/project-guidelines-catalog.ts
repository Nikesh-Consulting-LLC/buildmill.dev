export type CatalogSectionKey =
  | "overview"
  | "tech-stack"
  | "commands"
  | "run-commands"
  | "code-style"
  | "things-to-avoid"
  | "architecture"
  | "file-structure"
  | "testing"
  | "environment"
  | "git-pr"
  | "monorepo"
  | "doc-links"
  | "known-issues"
  | "boundaries"
  | "preferred-libs"
  | "good-patterns"
  | "agent-workflows"
  | "release"
  | "deployment"
  | "buildmill-workflow";

export type GuidelineCatalogEntry = {
  key: CatalogSectionKey;
  title: string;
  essential: boolean;
  guidance: string;
};

// Essentials first, then the story's table order — this is the order the
// "Add section" dropdown lists not-yet-added sections in.
export const GUIDELINE_CATALOG: GuidelineCatalogEntry[] = [
  {
    key: "tech-stack",
    title: "Tech stack",
    essential: true,
    guidance:
      "Languages, frameworks, key libraries; versions where they matter. State them explicitly.",
  },
  {
    key: "commands",
    title: "Commands",
    essential: true,
    guidance:
      "Exact commands for build, test, lint, dev server, migrations — whatever gets run often.",
  },
  {
    // us-5.9: surfaced prominently in every agent work context so workers
    // can verify their own work before submitting.
    key: "run-commands",
    title: "Run commands",
    essential: true,
    guidance:
      "How an agent verifies its work before submitting — the exact build, test, and lint commands (plus setup/install if a fresh checkout needs it first). One per line, e.g. `npm run build`.",
  },
  {
    key: "code-style",
    title: "Code style and conventions",
    essential: true,
    guidance:
      "Naming, formatting, preferred patterns — anything a linter doesn't enforce but you still care about.",
  },
  {
    key: "things-to-avoid",
    title: "Things to avoid",
    essential: true,
    guidance:
      "Known footguns, deprecated patterns, files not to touch, tempting-but-wrong APIs.",
  },
  {
    key: "overview",
    title: "Project overview",
    essential: false,
    guidance:
      "A few sentences on what the project is and does — enough that a fresh session isn't guessing at the domain.",
  },
  {
    key: "architecture",
    title: "Architecture notes",
    essential: false,
    guidance:
      "How the pieces fit, where core logic lives, non-obvious design decisions.",
  },
  {
    key: "file-structure",
    title: "File/directory structure",
    essential: false,
    guidance: "Only if non-standard or large enough that navigation isn't obvious.",
  },
  {
    key: "testing",
    title: "Testing expectations",
    essential: false,
    guidance: "How tests are run, what should be tested, coverage expectations if any.",
  },
  {
    key: "environment",
    title: "Environment setup",
    essential: false,
    guidance: "Env vars, secrets handling, local quirks (docker compose, ports, seed data).",
  },
  {
    key: "git-pr",
    title: "Git/PR conventions",
    essential: false,
    guidance:
      "Branch naming, commit format, PRs vs direct push — anything affecting how changes are delivered.",
  },
  {
    key: "monorepo",
    title: "Monorepo/multi-package notes",
    essential: false,
    guidance: "Which commands run at root vs inside a specific package.",
  },
  {
    key: "doc-links",
    title: "Links to other docs",
    essential: false,
    guidance: "Point to ADRs, API specs, design docs — a hub, not a copy of everything.",
  },
  {
    key: "known-issues",
    title: "Known issues or WIP areas",
    essential: false,
    guidance:
      "Modules mid-refactor or intentionally messy, so agents don't \"fix\" what's deliberately in flux.",
  },
  {
    key: "boundaries",
    title: "Permissions or boundaries",
    essential: false,
    guidance:
      "e.g. \"never modify /generated\", \"don't touch merged migrations\", \"ask before adding dependencies\".",
  },
  {
    key: "preferred-libs",
    title: "Preferred libraries",
    essential: false,
    guidance:
      "Explicit picks over alternatives (date-fns not moment) so choices aren't re-inferred each session.",
  },
  {
    key: "good-patterns",
    title: "Examples of good patterns",
    essential: false,
    guidance:
      "Point at specific files as reference implementations — concrete examples beat abstract rules.",
  },
  {
    key: "agent-workflows",
    title: "Subagent or workflow notes",
    essential: false,
    guidance:
      "Custom slash commands, subagents, multi-step workflows, and when to use them.",
  },
  {
    // us-7.4: seeded by the factory; describes the V<epic>.<seq> version
    // scheme, tagging, changelog, and UAT→Production promotion.
    key: "release",
    title: "Versioning & Release",
    essential: false,
    guidance:
      "How this project versions and ships — the V<epic>.<release-seq> scheme, git tagging, release notes, and UAT→Production promotion. Seeded by the factory; edit as you like.",
  },
  {
    // us-43.4: written by the guidelines refresh from the repo's own CI
    // workflows, container files and infra directories. `release` above
    // describes VERSIONING; this describes how the thing actually ships,
    // and it is the grounding us-1.51's deploy-script drafting never had.
    key: "deployment",
    title: "Deployment and Release",
    essential: false,
    guidance:
      "How this project ships: the environments and what runs on each, how a release is cut, and the deploy steps — each claim tied to the file it came from (a CI workflow, a Dockerfile, an infra directory). Prose, not a script.",
  },
  {
    key: "buildmill-workflow",
    title: "Working with Build Mill",
    essential: false,
    guidance:
      "How development flows through Build Mill — seeded by the factory on every project; edit, reorder, or delete as you like.",
  },
];

export const ESSENTIAL_SECTION_KEYS: CatalogSectionKey[] = GUIDELINE_CATALOG.filter(
  (s) => s.essential
).map((s) => s.key);

export const CUSTOM_SECTION_KEY = "custom" as const;

// AI-drafted section proposal from the guidelines brainstorming chatbot
// (US-1.52) — section_key is a CatalogSectionKey or "custom".
export type AiGuidelineSectionDraft = {
  section_key: string;
  title: string;
  content: string;
};
