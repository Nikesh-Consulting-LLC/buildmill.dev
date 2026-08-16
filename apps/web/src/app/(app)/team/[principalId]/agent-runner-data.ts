"use client";

// US-32.1: the data layer the two agent pages share. Watching an agent work
// and deciding how it should work are different tasks, so they are different
// pages now — the live console at `runner/` and the settings form at
// `settings/`. Both read the same rows off the same principal, so the loading
// lives here rather than in either page: neither owns the queries and the two
// cannot drift apart as the phase adds knobs.

import { useCallback, useEffect, useState } from "react";

import { ROLE_KINDS } from "@/lib/agent-roles";
import { createClient } from "@/lib/supabase/client";
import type { Database } from "@/lib/supabase/database.types";

export type Worker = Pick<
  Database["public"]["Tables"]["workers"]["Row"],
  "id" | "name" | "type" | "status"
>;
export type Config = Database["public"]["Tables"]["runner_config"]["Row"];
export type Session = Database["public"]["Tables"]["runner_sessions"]["Row"];
export type Command = Database["public"]["Tables"]["runner_command_audit"]["Row"];
export type Incident = Database["public"]["Tables"]["runner_incidents"]["Row"];
export type CurrentRun = {
  id: string;
  worker_id: string;
  kind: string;
  title: string;
  /** US-39.3: the item itself, so the console can link to the work rather than
   * only naming it. */
  issueId: string | null;
  /** US-39.3: when this run started, for "how long has it been doing this". */
  startedAt: string | null;
  /** US-39.3: the newest line us-39.1 streamed into the run trace — the real
   * answer to "what is it doing right now". Null before the first line
   * arrives, or for a run that predates progress streaming. */
  activity: string | null;
};

export type Health = "healthy" | "degraded" | "unhealthy";

// US-26.10: the machine this agent runs on, when Build Mill owns its
// lifecycle. Null for an agent someone installed by hand — which is why that
// one has no fleet controls, and the console says so rather than leaving their
// absence to be read as a bug.
export type AgentSlot = {
  id: string;
  slotIndex: number;
  /** The `agent_servers` row — what the fleet API's slot actions are keyed on. */
  hostId: string;
  /** US-35.2: the machine itself, which is what the UI now links to. The two
   *  ids are not interchangeable: the API takes the host, the page takes the
   *  machine. */
  serverId: string | null;
  hostName: string;
  paused: boolean;
};

// US-13.8: the module registry, with one line each — mirrors the API's
// KNOWN_MODULES and the supervisor's built-ins (US-10.5).
//
// US-77.2: `offered` is what the Add Agent wizard may create. An entry
// without it still resolves a label and its help text — agents already
// running that module keep reading correctly on their settings page, and the
// runner still accepts it — it is simply not a choice for a NEW agent.
// Re-offering one is this one flag, not a rewrite.
export const MODULES: {
  key: string;
  label: string;
  help: string;
  offered?: boolean;
  /** US-78.6: this module may only run on a platform agent pool. The wizard
   * hides the owned-machine branch for it; the API and a database trigger
   * enforce it, because a UI-only restriction is not a restriction. */
  poolOnly?: boolean;
}[] = [
  { key: "claude", label: "Claude Code", help: "Anthropic's coding agent CLI — the default worker." },
  // US-60.1: Claude Code under a platform-billed name — no license or key
  // to bring, billed to the platform's own key instead of the org's.
  { key: "buildmill", label: "Buildmill Agent", help: "Claude Code, billed to the platform's own key — zero setup." },
  { key: "grok", label: "Grok Build", help: "xAI's coding agent CLI.", offered: true },
  { key: "opencode", label: "OpenCode", help: "The open-source coding agent CLI.", offered: true },
  // US-78.3: the only agent type that holds a live session you can watch and
  // type into. Platform-managed model, platform pools only (US-78.5/78.6).
  {
    key: "interactive",
    label: "Buildmill Interactive Agent",
    help: "Runs a live session you can watch and steer. Platform model, on a Build Mill pool.",
    offered: true,
    poolOnly: true,
  },
  { key: "sim", label: "Simulator", help: "A deterministic no-LLM module for testing the pipeline." },
];

/** US-77.2: the agent types a new agent can be created as. The superadmin's
 * catalog (us-57.6) narrows this further at runtime; it never widens it. */
export const OFFERED_MODULES = MODULES.filter((m) => m.offered);

/** The dispatchable kinds a preset or model override applies to — every run
 * kind whose execution a manager can route. NOT the same list as
 * `lib/run-kinds.ts`: that one is the capability matrix's columns, and
 * `guidelines`/`elaborate` are deliberately not capabilities (migration 178).
 *
 * US-77.1: derived from `AGENT_ROLES` rather than written out again, so a kind
 * cannot be routable here and role-less in the UI that grants it — which is
 * exactly how `wireframe` was dispatchable-but-unroutable until us-53.4. Kept
 * in step with the API's ROUTE_KINDS in runner_socket.py; the web test
 * `agent-roles.test.ts` reads that tuple off disk and fails if they diverge. */
export const DISPATCH_KINDS: { key: string; label: string }[] = ROLE_KINDS;

/** The same list plus the runner's own brain, which is reasoning rather than
 * a run and so is never a checkbox. */
export const ROUTE_KINDS: { key: string; label: string }[] = [
  { key: "brain", label: "Runner brain" },
  ...ROLE_KINDS,
];

// US-32.4: the canonical setting names, in the order a manager reads them.
// Mirrors `KNOWN_SETTINGS` in the runner's modules/base.py and the API's
// runner_socket.py — a module says which of these it understands.
export const SETTING_LABELS: Record<string, string> = {
  model: "Model",
  fallback_model: "Fallback model",
  effort: "Reasoning effort",
  max_turns: "Turn ceiling",
  standing_instructions: "Standing instructions",
  mcp: "Factory MCP tools",
  auth: "Billing (API / subscription)",
};
// US-47.1: `permission_mode` is no longer one of them. An older runner that
// still declares it renders nothing here, which is the point — a label for a
// knob nobody reads is the control that appears to work.
export const SETTING_ORDER = [
  "model",
  "fallback_model",
  "effort",
  "max_turns",
  "standing_instructions",
  "mcp",
  "auth",
];

// US-52.1: how the `auth` values read on screen — the names the request used.
// US-60.1: `platform` never appears in either picker — it is forced by the
// server the moment `enabled_modules` is `['buildmill']`, never a choice —
// but the label exists so nothing renders blank if it's ever displayed.
export const AUTH_LABELS: Record<string, string> = {
  api: "Claude Code — API (metered)",
  subscription: "Claude Code — OAuth (subscription)",
  platform: "Buildmill Agent — platform key",
};

export type Knob = {
  name: string;
  kind: string;
  delivery: string;
  flag: string;
  choices: string[];
  help: string;
};

export type ModuleDeclaration = {
  module: string;
  capabilities: string[];
  needs_repo: boolean;
  settings: Knob[];
};

// US-32.5 / US-32.6: the org's presets, and how many agents point a route at
// each — the blast radius of editing one, visible before the edit.
export type Preset = {
  id: string;
  name: string;
  description: string;
  model: string | null;
  settings: Record<string, unknown>;
  version: number;
  is_default: boolean;
};

/** A route entry: a preset reference, or inline custom settings. */
export type RunRoute =
  | { preset_id: string }
  | { custom: Record<string, unknown> };

export const POLICY_MODES: { key: string; label: string; help: string }[] = [
  {
    key: "allow",
    label: "Allow",
    help: "The runner may execute any shell command, fully audited server-side. An empty policy means exactly this.",
  },
  {
    key: "require-approval",
    label: "Require approval",
    help: "Commands are held for a manager decision unless an allow pattern matches. (The interactive approval UX is a future story — unmatched commands are refused.)",
  },
  {
    key: "deny",
    label: "Deny",
    help: "Kill switch: every command is refused.",
  },
];

export function healthFor(workerId: string, incidents: Incident[]): Health {
  const since = Date.now() - 3600_000;
  const n = incidents.filter(
    (i) => i.worker_id === workerId && new Date(i.created_at).getTime() > since,
  ).length;
  return n >= 3 ? "unhealthy" : n >= 1 ? "degraded" : "healthy";
}

export const HEALTH_STYLES: Record<Health, string> = {
  healthy: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300",
  degraded: "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300",
  unhealthy: "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300",
};

export type AgentRunnerData = {
  loading: boolean;
  name: string;
  /** US-87.5: this agent's own org, whichever workspace the viewer has active.
   *  us-109.1: also what the settings page's Remove writes against. */
  orgId: string;
  workers: Worker[];
  otherWorkers: Worker[];
  slot: AgentSlot | null;
  configs: Config[];
  sessions: Session[];
  runs: CurrentRun[];
  commands: Command[];
  incidents: Incident[];
  orgModels: string[];
  modelProviders: Record<string, { name: string; type: string }>;
  /** US-32.4: what each module on this agent's machine declared it accepts,
   *  from the most recent session — connected or not. */
  declarations: ModuleDeclaration[];
  /** US-32.5: the org's presets, for the route table's pickers. */
  presets: Preset[];
  /** US-32.6: how many agents point a route at each preset, by preset id. */
  presetUsage: Record<string, number>;
  reload: () => Promise<void>;
};

/**
 * Everything both agent pages read. `activity` adds the live half — the
 * current run, the command feed, the incident list, and a realtime
 * subscription that refreshes on any of them. The settings page asks for it
 * off, because a form has no reason to re-render every time a command starts.
 */
export function useAgentRunner(
  principalId: string,
  { activity = true }: { activity?: boolean } = {},
): AgentRunnerData {
  const [name, setName] = useState("Agent");
  /** US-87.5: this agent's own org, discovered in `reload` and used to scope
   * the live subscriptions to one workspace's rows. */
  const [orgId, setOrgId] = useState("");
  const [workers, setWorkers] = useState<Worker[]>([]);
  const [otherWorkers, setOtherWorkers] = useState<Worker[]>([]);
  const [configs, setConfigs] = useState<Config[]>([]);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [runs, setRuns] = useState<CurrentRun[]>([]);
  const [commands, setCommands] = useState<Command[]>([]);
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [orgModels, setOrgModels] = useState<string[]>([]);
  // US-27.8: which provider each model belongs to. The gateway resolves a CLI
  // module's provider FROM the model, so the manager picking a route is
  // choosing a provider — the editor says which one instead of leaving it to
  // be discovered 90 seconds into a run.
  const [modelProviders, setModelProviders] = useState<
    Record<string, { name: string; type: string }>
  >({});
  const [declarations, setDeclarations] = useState<ModuleDeclaration[]>([]);
  const [presets, setPresets] = useState<Preset[]>([]);
  const [presetUsage, setPresetUsage] = useState<Record<string, number>>({});
  const [slot, setSlot] = useState<AgentSlot | null>(null);
  const [loading, setLoading] = useState(true);

  const reload = useCallback(async () => {
    const supabase = createClient();
    const p = await supabase
      .from("principals")
      .select("display_name, email")
      .eq("id", principalId)
      .maybeSingle();
    setName(p.data?.display_name || p.data?.email || "Agent");

    const w = await supabase
      .from("workers")
      .select("id,name,type,status,org_id")
      .eq("principal_id", principalId);
    const all = w.data ?? [];
    const workerList = all.filter((x) => x.type === "autonomous");
    setWorkers(workerList);
    setOtherWorkers(all.filter((x) => x.type !== "autonomous"));
    // This agent's own org — fixed regardless of which workspace the viewer
    // has active in the switcher, and needed below to scope org-owned reads
    // (llm_providers, presets) to it instead of every org the viewer belongs to.
    const orgId = (all[0] as { org_id?: string } | undefined)?.org_id;
    // US-87.5: also held in state so the live subscriptions below can name
    // their rows. Until it is known they do not subscribe at all — an
    // unfiltered subscription is what this story is removing, so falling back
    // to one would defeat the point.
    setOrgId(orgId ?? "");

    const slotRow = await supabase
      .from("agent_slots")
      .select("id, slot_index, agent_server_id, worker_id, agent_servers(servers(id, name))")
      .eq("principal_id", principalId)
      .eq("status", "active")
      .maybeSingle();
    if (slotRow.data) {
      // PostgREST embeds come back as an object or a one-element array
      // depending on the relationship the generator inferred; unwrap both.
      type Srv = { id?: string; name?: string };
      const embedded = slotRow.data as unknown as {
        agent_servers?: { servers?: Srv | Srv[] | null }
          | { servers?: Srv | Srv[] | null }[]
          | null;
      };
      const host = Array.isArray(embedded.agent_servers)
        ? embedded.agent_servers[0]
        : embedded.agent_servers;
      const srv = Array.isArray(host?.servers) ? host?.servers[0] : host?.servers;
      const cfg = await supabase
        .from("runner_config")
        .select("paused")
        .eq("worker_id", slotRow.data.worker_id as string)
        .maybeSingle();
      setSlot({
        id: slotRow.data.id as string,
        slotIndex: slotRow.data.slot_index as number,
        hostId: slotRow.data.agent_server_id as string,
        serverId: srv?.id ?? null,
        hostName: srv?.name ?? "a machine",
        paused: cfg.data?.paused ?? true,
      });
    } else {
      setSlot(null);
    }

    // US-13.8: the org's configured models feed the route dropdowns — scoped
    // to this agent's own org, not every org the viewer belongs to (the
    // query previously had no org filter at all, so a multi-org viewer saw
    // every workspace's configured models mixed into one list).
    if (orgId) {
      const prov = await supabase
        .from("llm_providers")
        .select("name, provider_type, models, default_model")
        .eq("org_id", orgId);
      const models = new Set<string>();
      const owners: Record<string, { name: string; type: string }> = {};
      for (const row of prov.data ?? []) {
        const owner = {
          name: (row.name as string) ?? "",
          type: (row.provider_type as string) ?? "",
        };
        for (const m of (row.models as string[] | null) ?? []) {
          models.add(m);
          owners[m] = owner;
        }
        if (row.default_model) {
          models.add(row.default_model as string);
          owners[row.default_model as string] ??= owner;
        }
      }
      setOrgModels([...models].sort());
      setModelProviders(owners);
    } else {
      setOrgModels([]);
      setModelProviders({});
    }

    // US-32.5/32.6: the org's presets, and the blast radius of each.
    if (orgId) {
      const [presetRows, configRows] = await Promise.all([
        supabase
          .from("agent_presets")
          .select("id, name, description, model, settings, version, is_default")
          .eq("org_id", orgId)
          .is("archived_at", null)
          .order("sort_order", { ascending: true }),
        supabase.from("runner_config").select("run_routes").eq("org_id", orgId),
      ]);
      setPresets((presetRows.data ?? []) as unknown as Preset[]);
      const usage: Record<string, number> = {};
      for (const row of configRows.data ?? []) {
        const routes = (row.run_routes as Record<string, RunRoute> | null) ?? {};
        for (const entry of Object.values(routes)) {
          const id = (entry as { preset_id?: string })?.preset_id;
          if (id) usage[id] = (usage[id] ?? 0) + 1;
        }
      }
      setPresetUsage(usage);
    }

    const ids = workerList.map((x) => x.id);
    if (ids.length === 0) {
      setConfigs([]);
      setSessions([]);
      setRuns([]);
      setCommands([]);
      setIncidents([]);
      setDeclarations([]);
      setLoading(false);
      return;
    }

    const [c, s] = await Promise.all([
      supabase.from("runner_config").select("*").in("worker_id", ids),
      supabase
        .from("runner_sessions")
        .select("*")
        .in("worker_id", ids)
        .is("disconnected_at", null),
    ]);
    setConfigs(c.data ?? []);
    setSessions(s.data ?? []);

    // US-32.4: the declarations come off the most recent session, connected or
    // not — a module's knobs are knowable while its machine is down, and a
    // settings page that goes blank when an agent restarts is useless.
    const decl = await supabase
      .from("runner_sessions")
      .select("worker_id, module_settings, connected_at")
      .in("worker_id", ids)
      .order("connected_at", { ascending: false })
      .limit(20);
    const seen = new Set<string>();
    const merged: ModuleDeclaration[] = [];
    for (const row of decl.data ?? []) {
      const workerId = row.worker_id as string;
      if (seen.has(workerId)) continue; // only the latest session per worker
      seen.add(workerId);
      for (const entry of (row.module_settings as unknown as
        | ModuleDeclaration[]
        | null) ?? []) {
        if (!merged.some((m) => m.module === entry.module)) merged.push(entry);
      }
    }
    setDeclarations(merged);

    if (!activity) {
      setLoading(false);
      return;
    }

    const [r, cmd, inc] = await Promise.all([
      supabase
        .from("runs")
        .select(
          "id, worker_id, kind, issue_id, started_at, issues!runs_issue_org_fk(title)"
        )
        .in("worker_id", ids)
        .eq("status", "running"),
      supabase
        .from("runner_command_audit")
        .select("*")
        .in("worker_id", ids)
        .order("started_at", { ascending: false })
        .limit(60),
      supabase
        .from("runner_incidents")
        .select("*")
        .in("worker_id", ids)
        .order("created_at", { ascending: false })
        .limit(30),
    ]);
    // US-39.3: the newest trace line per running run — the real answer to
    // "what is it doing right now", now that us-39.1 streams the agent's steps
    // into run_trace instead of leaving the launch command as the only clue.
    const runRows = r.data ?? [];
    const activityByRun = new Map<string, string>();
    if (runRows.length) {
      const { data: traces } = await supabase
        .from("run_trace")
        .select("run_id, content, created_at")
        .in("run_id", runRows.map((row) => row.id as string))
        .order("created_at", { ascending: false })
        .limit(60);
      for (const t of (traces ?? []) as { run_id: string; content: string }[]) {
        // Ordered newest-first, so the first one seen per run is the latest.
        if (!activityByRun.has(t.run_id)) activityByRun.set(t.run_id, t.content);
      }
    }
    setRuns(
      runRows.map((row) => ({
        id: row.id as string,
        worker_id: row.worker_id as string,
        kind: row.kind as string,
        title:
          (row.issues as unknown as { title: string } | null)?.title ?? "work item",
        issueId: (row.issue_id as string | null) ?? null,
        startedAt: (row.started_at as string | null) ?? null,
        activity: activityByRun.get(row.id as string) ?? null,
      })),
    );
    setCommands(cmd.data ?? []);
    setIncidents(inc.data ?? []);
    setLoading(false);
  }, [principalId, activity]);

  useEffect(() => {
    void reload();
    // US-32.6: a preset edited on Settings → Run presets is reflected here
    // without a reload — a route pointing at `Deep` should not show yesterday's
    // Deep. Subscribed even when the activity half is off, because this is the
    // settings page's own liveness, not the console's.
    // US-87.5: nothing subscribes before the agent's org is known — every
    // subscription here names its rows, and an unfiltered fallback would
    // reinstate exactly the fan-out this removes. `reload` sets it on its
    // first pass, which re-runs this effect.
    if (!orgId) return;
    const org = `org_id=eq.${orgId}`;

    const presetClient = createClient();
    const presetChannel = presetClient
      .channel(`agent-presets-${principalId}`)
      .on(
        "postgres_changes",
        { event: "*", schema: "public", table: "agent_presets", filter: org },
        () => void reload(),
      )
      .subscribe();
    if (!activity) {
      return () => {
        void presetClient.removeChannel(presetChannel);
      };
    }
    // Live: any command / run / presence change refreshes the console.
    const supabase = createClient();
    const channel = supabase
      .channel(`agent-runner-${principalId}`)
      .on("postgres_changes", { event: "*", schema: "public", table: "runner_command_audit", filter: org }, () => void reload())
      .on("postgres_changes", { event: "*", schema: "public", table: "runs", filter: org }, () => void reload())
      .on("postgres_changes", { event: "*", schema: "public", table: "runner_sessions", filter: org }, () => void reload())
      .on("postgres_changes", { event: "*", schema: "public", table: "runner_incidents", filter: org }, () => void reload())
      .subscribe();
    return () => {
      void supabase.removeChannel(channel);
      void presetClient.removeChannel(presetChannel);
    };
  }, [reload, principalId, activity, orgId]);

  return {
    loading,
    name,
    orgId,
    workers,
    otherWorkers,
    slot,
    configs,
    sessions,
    runs,
    commands,
    incidents,
    orgModels,
    modelProviders,
    declarations,
    presets,
    presetUsage,
    reload,
  };
}
