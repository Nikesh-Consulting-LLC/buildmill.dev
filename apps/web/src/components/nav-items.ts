import {
  Activity,
  CircleHelp,
  Cpu,
  Factory,
  FlaskConical,
  LayoutDashboard,
  FolderGit2,
  ListTodo,
  MessageSquareWarning,
  Rocket,
  ScrollText,
  Server,
  Settings,
  SlidersHorizontal,
  UserCog,
  Users,
  type LucideIcon,
} from "lucide-react";

export type NavChild = { href: string; label: string };

// US-62.11: a disclosure's children can carry their own small group headings
// — the SuperAdmin submenu grew to eleven flat links and needed the same
// heading device the top-level sidebar already uses (`{ heading }` in
// `NavEntry`), just nested one level deeper.
export type NavChildEntry = NavChild | { heading: string };

export type NavItem = {
  href: string;
  label: string;
  icon: LucideIcon;
  children?: NavChildEntry[];
};

// A nav entry is a link, a section heading, or a separator — so the sidebar
// can group "work" vs "configure" vs "team/settings" vs superadmin.
export type NavEntry = NavItem | { heading: string } | { separator: true };

// US-2.24: settings sections are a submenu of the Settings entry in the
// main navigation (sidebar + mobile drawer), not an in-page rail.
// US-9.13/9.14: Workers, My Access Tokens, and My Team all fold into the
// top-level Team surface.
export const SETTINGS_ITEMS: NavChild[] = [
  { href: "/settings/llm-providers", label: "LLM Providers" },
  // US-57.7: run presets are platform-authored now (US-57.6) — the org nav
  // entry retires with the page; /admin/preset-templates is the superadmin's.
  // US-33.1/33.3: what the factory costs, and the rates it is costed at.
  { href: "/settings/spend", label: "Spend" },
  // US-34.1: the MCP servers agents may be granted.
  { href: "/settings/tools", label: "Tool servers" },
  { href: "/settings/github", label: "GitHub" },
  { href: "/settings/notifications", label: "Notifications" },
  // Phase 67 (us-67.3): copy/fine-tune the superadmin's project templates.
  { href: "/settings/project-templates", label: "Project templates" },
];

// US-5.17: the admin console gains a submenu alongside the Settings one.
// US-62.11: eleven flat links was too large a single list to scan — grouped
// into four named sections (requested 2026-08-01): who has access, what
// things cost / how they're performing, the machines and how agents run on
// them, and the platform's own operational settings.
// US-91.10: the superadmin's fifteen links were one disclosure with four
// inline heading groups — every visit cost "open the drawer, then find the
// row", and while open it pushed the rest of the nav off screen. The groups
// are promoted to menus of their own under a `SuperAdmin` section heading.
//
// `ADMIN_ENTRIES` is what the shell splices in; `ADMIN_ITEMS` is kept as the
// flat list of every superadmin destination, which the active-route test and
// any future audit can read without walking the tree.
export const ADMIN_ACCOUNTS: NavChild[] = [
  { href: "/admin/orgs", label: "Orgs" },
  { href: "/admin/users", label: "Users" },
  { href: "/admin/roles", label: "Roles" },
];

export const ADMIN_SETTINGS: NavChild[] = [
  // US-57.6: how every agent runs, and which modules exist to choose from.
  { href: "/admin/run-config", label: "How agents run" },
  // US-32.5: the preset templates each org is seeded from.
  { href: "/admin/preset-templates", label: "Preset templates" },
  // US-57.16: the org's own presets — model, effort, ceilings, tool grants.
  { href: "/admin/presets", label: "Presets" },
  { href: "/admin/prompt-templates", label: "Prompt templates" },
  // Phase 67 (us-67.2): the bundle a new project silently inherits a copy of.
  { href: "/admin/project-templates", label: "Project templates" },
];

export const ADMIN_LOGS: NavChild[] = [
  // US-16.9: what the factory itself has reported, across every org.
  { href: "/admin/system-issues", label: "System issues" },
  // US-79.8: every agent failure on every workspace — including the deaths
  // that never raise an exception (lease expiry, stale heartbeat).
  { href: "/admin/agent-failures", label: "Agent failures" },
  // US-60.2: every org's API usage, across every customer at once.
  { href: "/admin/usage", label: "Usage" },
  // US-62.1: every run, sliced by kind/project/org/agent, for tuning timeouts.
  { href: "/admin/analytics/runs", label: "Task runs" },
  // US-62.4/US-62.5: a human's work, and how long each gate waited for them.
  { href: "/admin/analytics/people", label: "People" },
  // US-62.9: frontend/API/database/LLM latency, read together.
  { href: "/admin/analytics/performance", label: "Performance" },
];

export const ADMIN_ENTRIES: NavEntry[] = [
  { heading: "SuperAdmin" },
  // US-57.1: the machines the platform provisions as agent pools.
  { href: "/admin/machines", label: "Machines", icon: Cpu },
  {
    href: "/admin/orgs",
    label: "Accounts",
    icon: UserCog,
    children: ADMIN_ACCOUNTS,
  },
  {
    href: "/admin/run-config",
    label: "Settings",
    icon: SlidersHorizontal,
    children: ADMIN_SETTINGS,
  },
  {
    href: "/admin/system-issues",
    label: "Logs",
    icon: ScrollText,
    children: ADMIN_LOGS,
  },
];

/** Every superadmin destination, flat — for the active-route test and audits. */
export const ADMIN_ITEMS: NavChild[] = [
  { href: "/admin/machines", label: "Machines" },
  ...ADMIN_ACCOUNTS,
  ...ADMIN_SETTINGS,
  ...ADMIN_LOGS,
];

// Grouped nav: the delivery/monitoring surfaces first, then a Configure
// group (Projects/Servers), then Team/Settings/Help. Superadmin is appended
// by the shell for platform admins only.
export const NAV_ITEMS: NavEntry[] = [
  // US-91.19: "Things to Do" described a list; the page is one desk you
  // work from now. The route stays /dashboard — renaming it would break
  // every link, bookmark and agent instruction for no gain.
  { href: "/dashboard", label: "Workdesk", icon: LayoutDashboard },
  { href: "/issues", label: "Work Items", icon: ListTodo },
  // US-91.19: what the factory is holding or working, in worker-pull order.
  // It was a tab on the workdesk restating this page; now it is the page.
  { href: "/factory-queue", label: "Factory Queue", icon: Factory },
  { href: "/tests", label: "Testing", icon: FlaskConical },
  // Release surface — presentation to be defined by later stories.
  { href: "/releases", label: "Release", icon: Rocket },
  // US-5.34: the org-wide "who did what, did any of it fail" feed.
  { href: "/activity", label: "Activity", icon: Activity },
  // US-16.6: what deployed apps have reported about themselves — cross-project,
  // like Work Items, because triage is one stop or it is not done.
  // US-91.7: named for what it holds (bugs, not analytics) and moved beside
  // Activity, the other "what happened out there" surface, so the four
  // delivery surfaces above it read as the pipeline in order.
  { href: "/reports", label: "Bug Reports", icon: MessageSquareWarning },
  { separator: true },
  { heading: "Configure" },
  { href: "/projects", label: "Projects", icon: FolderGit2 },
  // US-35.2: one entry for every machine the factory can reach. Agent servers
  // were a second entry for the same object — an `agent_servers` row is a
  // `servers` row that has been provisioned — so the difference is a lifecycle
  // the machine's own card and page now show, not a nav choice to get right.
  { href: "/servers", label: "Machines", icon: Server },
  // US-9.13: one surface for people + agents (replaces the Workers page).
  { href: "/team", label: "Team", icon: Users },
  { separator: true },
  { href: "/settings", label: "Settings", icon: Settings, children: SETTINGS_ITEMS },
  // US-2.30: the operator handbook.
  { href: "/help", label: "Help", icon: CircleHelp },
];
