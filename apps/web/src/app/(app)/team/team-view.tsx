"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import Link from "next/link";
import {
  Bot,
  ChevronDown,
  Cpu,
  TerminalSquare,
  KeyRound,
  Loader2,
  Pause,
  Play,
  SlidersHorizontal,
  SquareTerminal,
  User,
  UserMinus,
  Users,
} from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import { apiFetch } from "@/lib/api";
import { cn } from "@/lib/utils";
import { formatLastSeen } from "@/lib/format-time";
import { ROLES, ROLE_LABELS, type Role } from "@/lib/permissions";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/empty-state";
import { StatusBadge } from "@/components/status-badge";
import { confirmDialog } from "@/components/ui/confirm-dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  ProvisionMemberDialog,
  PasswordReveal,
} from "../settings/provision-member-dialog";
import { WorkersHelp } from "../settings/workers-help";
import { AddAgentWizard } from "./add-agent-wizard";
import {
  ConnectPanel,
  type ConnectPrincipal,
  type ConnectProject,
} from "./connect-panel";
import { openClaudeTerminal } from "./open-claude-terminal";
import { MODULES } from "./[principalId]/agent-runner-data";
import { MemberDetail } from "./[principalId]/member-detail";
import { RouterTokenPanel } from "./[principalId]/router-token-panel";

export type MemberRow = {
  principal_id: string;
  user_id: string | null;
  role: Role;
  status: "active" | "suspended";
  created_at: string;
  principals: {
    kind: "human" | "agent";
    email: string | null;
    display_name: string | null;
  } | null;
};

export type WorkerRow = {
  id: string;
  name: string;
  type: "autonomous" | "human";
  user_id: string | null;
  principal_id: string | null;
  token_last4: string;
  status: "active" | "revoked";
  last_seen_at: string | null;
  created_at: string;
  /** The single project this token's MCP endpoint serves — null means
   * unscoped (created before this column existed, or never assigned). */
  project_id: string | null;
  /** Opt-in, on by default: browse/read/download a project's repository
   * over MCP with no claimed run — get_project_tree, read_project_file,
   * get_project_workspace. Off only when a manager has deliberately
   * turned it off for this worker. */
  no_claim_checkout: boolean;
};

type Claim = {
  worker_id: string;
  title: string;
  kind: string;
  claimed_at?: string | null;
  last_heartbeat_at?: string | null;
  // US-35.3: enough to link to the run and name where the work lives.
  run_id?: string;
  issue_id?: string | null;
  project?: string | null;
};
// US-10.12: per-agent runner presence + derived health, keyed by principal id.
export type RunnerStatus = Record<
  string,
  { online: boolean; health: "healthy" | "degraded" | "unhealthy" }
>;

// US-35.1: a provisioned machine an agent can be given a seat on, for the
// Add-agent picker. Capacity is shown so the choice is informed rather than a
// name-guess.
/** US-35.2: where a managed agent sits. `hostId` keys the fleet API's slot
 *  actions; `serverId` is the machine page the UI links to. Named rather than
 *  inlined in three prop lists, which is how the third one came to be missed. */
export type AgentSeat = {
  hostId: string;
  serverId: string | null;
  hostName: string;
  slotIndex: number;
  /** US-55.6: the `agent_slots.id` — set only where a caller populates it
   *  (the roster), needed to target the Claude Terminal action at this
   *  slot's own OS user/workspace rather than the machine's login. */
  slotId?: string;
};

export type MachineOption = {
  /** The `agent_servers` row the add-agent job is posted against. */
  hostId: string;
  /** US-35.2: the machine's own id — where the manager is sent to watch it. */
  serverId: string | null;
  name: string;
  agentCount: number;
  lastProbeAt: string | null;
  /** US-53.2: what the machine's probe reported it can run — the wizard's
   *  What step offers only these. Empty = never declared, no filter. */
  modules: string[];
  /** US-53.2: the machine's connected Claude account marker, so the wizard's
   *  billing step can answer readiness before any slot exists. */
  claudeConnectedAt: string | null;
};

// US-35.1: why an agent is not working, keyed by principal id — the same
// computation the host-scoped route serves, so Team and the fleet page cannot
// disagree.
export type IdleReason = { reason: string; detail?: string };

/** US-35.1: "Idle" alone was the whole problem — presence is not permission.
 *  `working` and plain `idle` need no explanation; the rest are conditions a
 *  manager has to act on, so they read as such. Exported for the member page
 *  (US-53.3), which absorbed the drawer that used to render them here. */
export const IDLE_LABELS: Record<string, string> = {
  revoked: "Token revoked",
  paused: "Paused",
  "no-grants": "No project grants",
  "queue-held": "Queue held",
  idle: "Idle",
  working: "Working",
  unknown: "Unknown",
};

/** US-35.3: how long the current claim has been held. Ticks client-side from a
 *  server timestamp — a run that started 40 minutes ago should not read "just
 *  now" because the page was rendered then. */
function elapsedSince(iso: string | null | undefined, now: number): string | null {
  if (!iso) return null;
  const ms = now - new Date(iso).getTime();
  if (ms < 0) return null;
  const mins = Math.floor(ms / 60000);
  if (mins < 1) return "just started";
  if (mins < 60) return `${mins}m`;
  const hours = Math.floor(mins / 60);
  return `${hours}h ${mins % 60}m`;
}

export function idleTone(reason: string) {
  if (reason === "working") return "text-emerald-600 dark:text-emerald-400";
  if (reason === "idle") return "text-muted-foreground";
  // Everything else is a condition that stops work happening.
  return "text-amber-600 dark:text-amber-400";
}

function formatWhen(iso: string) {
  return new Date(iso).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

export function principalName(m: MemberRow) {
  return m.principals?.display_name || m.principals?.email || m.principal_id;
}

export function TeamView({
  orgId,
  canManage,
  members,
  workers,
  claims,
  projects,
  orgShortname,
  snippets,
  runnerStatus,
  hostByPrincipal,
  machines,
  assignedByPrincipal,
  maxAgents,
  claudeBillingByPrincipal,
  moduleByPrincipal,
  runningRunByPrincipal,
  canManageOrg,
  myPrincipalId,
  initialExpandedId,
}: {
  orgId: string;
  canManage: boolean;
  members: MemberRow[];
  workers: WorkerRow[];
  claims: Claim[];
  projects: { id: string; name: string; slug: string }[];
  orgShortname: string;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  snippets: any;
  runnerStatus: RunnerStatus;
  // US-26.10: principal id -> the agent server it runs on, for agents whose
  // lifecycle Build Mill owns.
  hostByPrincipal: Record<string, AgentSeat>;
  machines: MachineOption[];
  /** US-35.3: open items assigned to each principal, people and agents alike. */
  assignedByPrincipal: Record<string, number>;
  /** US-57.2: the org's agent quota — the roster derives "used" from `members` itself. */
  maxAgents: number;
  /** US-55.6: 'subscription' | 'api' | 'platform' | null per principal — the
   *  roster's Claude Terminal action only exists for 'subscription'. */
  claudeBillingByPrincipal: Record<string, string | null>;
  /** Which CLI module(s) each agent runs — the badge next to its name. */
  moduleByPrincipal: Record<string, string[]>;
  /** US-78.11: principal -> the run it is holding right now, or absent.
   *  Drives the CLI button's glow. */
  runningRunByPrincipal: Record<string, string>;
  /** US-55.6: gates the Claude Terminal action, same as the agent's own page. */
  canManageOrg: boolean;
  /** The signed-in user's own principal id, for "your own token" wording. */
  myPrincipalId: string | null;
  /** A deep link (`/team/{id}`, `?principal=`) lands here — expand that row. */
  initialExpandedId: string | null;
}) {
  const router = useRouter();

  // US-35.1: why each agent is idle, from the org-scoped read the fleet page's
  // host-scoped route shares.
  const [idleReasons, setIdleReasons] = useState<Record<string, IdleReason>>({});
  useEffect(() => {
    let cancelled = false;
    apiFetch(`/api/v1/agents/idle-reasons?org=${orgId}`)
      .then((d) => {
        if (!cancelled) setIdleReasons(d?.by_principal ?? {});
      })
      // Best-effort: an agent's reason is context, and failing to fetch it must
      // not take the roster down with it.
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [orgId]);

  // US-10.12: keep the roster's runner status pills live.
  useEffect(() => {
    const supabase = createClient();
    // US-87.5: every subscription names its rows. Unfiltered, each of these
    // decoded and RLS-evaluated every runner session, incident and run in
    // EVERY workspace just to trigger a refresh of this one.
    const org = `org_id=eq.${orgId}`;
    const channel = supabase
      .channel(`team-runner-presence-${orgId}`)
      .on(
        "postgres_changes",
        { event: "*", schema: "public", table: "runner_sessions", filter: org },
        () => router.refreshSilently(),
      )
      .on(
        "postgres_changes",
        { event: "*", schema: "public", table: "runner_incidents", filter: org },
        () => router.refreshSilently(),
      )
      // US-35.3: a claim starting or ending is the Live tab's whole subject —
      // it must not need a manual refresh to appear.
      .on(
        "postgres_changes",
        { event: "*", schema: "public", table: "runs", filter: org },
        () => router.refreshSilently(),
      )
      .subscribe();
    return () => {
      void supabase.removeChannel(channel);
    };
  }, [router, orgId]);

  const workersByPrincipal = useMemo(() => {
    const map = new Map<string, WorkerRow[]>();
    for (const w of workers) {
      if (!w.principal_id) continue;
      const list = map.get(w.principal_id) ?? [];
      list.push(w);
      map.set(w.principal_id, list);
    }
    return map;
  }, [workers]);

  const claimByWorker = useMemo(
    () => new Map(claims.map((c) => [c.worker_id, c])),
    [claims]
  );

  // US-10.13: every member's connect identity, for the per-row expand panel.
  const principals = useMemo<ConnectPrincipal[]>(
    () =>
      members.map((m) => ({
        principalId: m.principal_id,
        name: principalName(m),
        kind: (m.principals?.kind ?? "human") as "human" | "agent",
        workerId: workersByPrincipal.get(m.principal_id)?.[0]?.id ?? null,
        // US-73.1: a person's email doubles as their git username.
        email: m.principals?.email ?? null,
      })),
    [members, workersByPrincipal],
  );

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-end gap-2">
        {canManage && (
          <>
            <ProvisionMemberDialog orgId={orgId} onChanged={() => router.refresh()} />
            {/* US-53.2: the wizard is the Add path — the old AddAgentDialog's
                abilities (identity, machine slot, self-hosted token) are all
                steps inside it, and the dialog itself is retired. */}
            <AddAgentWizard
              orgId={orgId}
              machines={machines}
              onChanged={() => router.refresh()}
              quota={{
                used: members.filter((m) => m.principals?.kind === "agent").length,
                max: maxAgents,
              }}
            />
          </>
        )}
      </div>

      <MemberList
        orgId={orgId}
        canManage={canManage}
        canManageOrg={canManageOrg}
        members={members}
        workersByPrincipal={workersByPrincipal}
        claimByWorker={claimByWorker}
        idleReasons={idleReasons}
        runnerStatus={runnerStatus}
        hostByPrincipal={hostByPrincipal}
        assignedByPrincipal={assignedByPrincipal}
        moduleByPrincipal={moduleByPrincipal}
        runningRunByPrincipal={runningRunByPrincipal}
        claudeBillingByPrincipal={claudeBillingByPrincipal}
        principals={principals}
        projects={projects as ConnectProject[]}
        orgShortname={orgShortname}
        myPrincipalId={myPrincipalId}
        initialExpandedId={initialExpandedId}
      />

      <WorkersHelp snippets={snippets} />
    </div>
  );
}

// ------------------------------------------------------------------ Members
/**
 * One row per principal — agents and people together, and the app's only
 * view of a member now. What used to be three surfaces (a Roster tab, a Live
 * tab, and a standalone member page under `/team/{id}`) is one row: roster
 * controls (role, suspend, remove), live status (busy/idle, what an agent is
 * working on right now), and a chevron that expands the row in place to that
 * member's full detail (tokens, project access, performance, history) and
 * connect instructions — the content the old member page rendered, now
 * inline instead of a second click-through.
 *
 * Rows key on principal and worker ids, never on names: names are editable
 * and deliberately non-unique (US-32.2).
 */
function MemberList({
  orgId,
  canManage,
  canManageOrg,
  members,
  workersByPrincipal,
  claimByWorker,
  idleReasons,
  runnerStatus,
  hostByPrincipal,
  assignedByPrincipal,
  moduleByPrincipal,
  runningRunByPrincipal,
  claudeBillingByPrincipal,
  principals,
  projects,
  orgShortname,
  myPrincipalId,
  initialExpandedId,
}: {
  orgId: string;
  canManage: boolean;
  canManageOrg: boolean;
  members: MemberRow[];
  workersByPrincipal: Map<string, WorkerRow[]>;
  claimByWorker: Map<string, Claim>;
  idleReasons: Record<string, IdleReason>;
  runnerStatus: RunnerStatus;
  hostByPrincipal: Record<string, AgentSeat>;
  assignedByPrincipal: Record<string, number>;
  moduleByPrincipal: Record<string, string[]>;
  /** US-78.11: principal -> the run it is holding right now, or absent.
   *  Drives the CLI button's glow. */
  runningRunByPrincipal: Record<string, string>;
  claudeBillingByPrincipal: Record<string, string | null>;
  principals: ConnectPrincipal[];
  projects: ConnectProject[];
  orgShortname: string;
  myPrincipalId: string | null;
  initialExpandedId: string | null;
}) {
  const router = useRouter();
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [resetReveal, setResetReveal] = useState<{
    email: string;
    password: string;
  } | null>(null);
  // The expanded row's detail panel — one at a time.
  const [expandedId, setExpandedId] = useState<string | null>(initialExpandedId);

  // A deep link (`/team/{id}`) lands with a row already expanded — scroll it
  // into view once so it isn't buried below the fold in a long roster.
  useEffect(() => {
    if (!initialExpandedId) return;
    document
      .getElementById(`member-${initialExpandedId}`)
      ?.scrollIntoView({ block: "center" });
    // Deliberately once, on mount — re-running on every render would fight a
    // manager who collapses the row and scrolls elsewhere.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Elapsed time has to move on its own; a claim's start is a fixed
  // timestamp, so without this a run reads as however old the page is.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(t);
  }, []);

  async function mutate(
    principalId: string,
    changes: Partial<Pick<MemberRow, "role" | "status">>
  ) {
    setError(null);
    setBusyId(principalId);
    const supabase = createClient();
    try {
      const { error: dbError } = await supabase
        .from("organization_members")
        .update(changes)
        .eq("org_id", orgId)
        .eq("principal_id", principalId);
      if (dbError) setError(dbError.message);
      else router.refresh();
    } finally {
      setBusyId(null);
    }
  }

  async function remove(principalId: string) {
    if (
      !(await confirmDialog({
        title: "Remove member?",
        description:
          "They lose access to this org and their tokens are revoked.",
        confirmLabel: "Remove",
        destructive: true,
      }))
    )
      return;
    setError(null);
    setBusyId(principalId);
    const supabase = createClient();
    try {
      const { error: dbError } = await supabase
        .from("organization_members")
        .delete()
        .eq("org_id", orgId)
        .eq("principal_id", principalId);
      if (dbError) setError(dbError.message);
      else router.refresh();
    } finally {
      setBusyId(null);
    }
  }

  async function resetPassword(userId: string, email: string) {
    if (
      !(await confirmDialog({
        title: "Generate a new password?",
        description: `A new one-time password for ${email} will be generated for you to hand off.`,
        confirmLabel: "Generate",
      }))
    )
      return;
    setError(null);
    setResetReveal(null);
    setBusyId(userId);
    try {
      const data = await apiFetch(
        `/api/v1/orgs/${orgId}/members/${userId}/reset-password`,
        { method: "POST" }
      );
      setResetReveal({ email, password: data.password });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusyId(null);
    }
  }

  if (!members.length) {
    return (
      <EmptyState
        icon={Users}
        title="Nobody here yet"
        description="Add an agent or invite a teammate to see live activity."
      />
    );
  }

  return (
    <div className="grid gap-4">
      <ul className="grid gap-2">
        {members.map((m) => {
          const isAgent = m.principals?.kind === "agent";
          const suspended = m.status === "suspended";
          const mine = workersByPrincipal.get(m.principal_id) ?? [];
          const tokenCount = mine.filter((w) => w.status === "active").length;
          const claim = mine.map((w) => claimByWorker.get(w.id)).find(Boolean);
          const idle = idleReasons[m.principal_id];
          const seat = hostByPrincipal[m.principal_id];
          const status = runnerStatus[m.principal_id];
          const assigned = assignedByPrincipal[m.principal_id] ?? 0;
          const revoked = mine.length > 0 && mine.every((w) => w.status === "revoked");
          const lastSeen = mine
            .map((w) => w.last_seen_at)
            .filter(Boolean)
            .sort()
            .pop() as string | undefined;
          const moduleKeys = moduleByPrincipal[m.principal_id] ?? [];
          const moduleLabel = MODULES.find((mod) => mod.key === moduleKeys[0])?.label;
          const expanded = expandedId === m.principal_id;
          const toggle = () =>
            setExpandedId((cur) => (cur === m.principal_id ? null : m.principal_id));

          return (
            <li key={m.principal_id} id={`member-${m.principal_id}`} className="rounded-md border">
              {/* US-68.6: the same shrink-0-cluster-squeezes-the-name bug
                  page-header.tsx had (us-68.5) — a role select plus up to six
                  icon buttons never shrink, so on a phone the name button was
                  the only side willing to give up width. Stacked below `sm`;
                  unchanged above it. */}
              <div className="flex flex-col gap-3 px-3 py-2 text-sm sm:flex-row sm:flex-wrap sm:items-center sm:justify-between">
                <button
                  type="button"
                  onClick={toggle}
                  className="flex min-w-0 flex-1 items-center gap-3 text-left hover:opacity-80"
                >
                  {isAgent ? (
                    <Bot
                      className={cn(
                        "size-4 shrink-0",
                        // Busy work is the whole point of an orange agent —
                        // a glance at the icon should say so before reading
                        // any text.
                        claim
                          ? "text-orange-500 dark:text-orange-400"
                          : "text-muted-foreground",
                      )}
                    />
                  ) : (
                    <User className="size-4 shrink-0 text-muted-foreground" />
                  )}
                  <span className="flex min-w-0 flex-col">
                    <span className="flex flex-wrap items-center gap-1.5 truncate font-medium">
                      {principalName(m)}
                      {isAgent && (
                        <span className="rounded-full border px-1.5 py-0.5 text-[10px] font-medium uppercase text-muted-foreground">
                          agent
                        </span>
                      )}
                      {isAgent && moduleLabel && (
                        <span className="rounded-full border px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
                          {moduleLabel}
                        </span>
                      )}
                      {isAgent && status && (
                        <>
                          <span
                            className={cn(
                              "inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[10px] font-medium",
                              status.online
                                ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300"
                                : "bg-muted text-muted-foreground",
                            )}
                          >
                            <span
                              className={cn(
                                "h-1.5 w-1.5 rounded-full",
                                status.online ? "bg-emerald-500" : "bg-muted-foreground",
                              )}
                            />
                            {status.online ? "online" : "offline"}
                          </span>
                          {status.health !== "healthy" && (
                            <span
                              className={cn(
                                "rounded-full px-1.5 py-0.5 text-[10px] font-medium",
                                status.health === "unhealthy"
                                  ? "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300"
                                  : "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300",
                              )}
                            >
                              {status.health}
                            </span>
                          )}
                        </>
                      )}
                      {suspended && (
                        <span className="rounded-full border border-amber-500/50 px-1.5 py-0.5 text-[10px] font-medium uppercase text-amber-600 dark:text-amber-400">
                          suspended
                        </span>
                      )}
                    </span>
                    <span className="truncate text-xs text-muted-foreground">
                      {m.principals?.email ? `${m.principals.email} · ` : ""}
                      {tokenCount} token{tokenCount === 1 ? "" : "s"} · Joined{" "}
                      {formatWhen(m.created_at)}
                      {/* US-32.2: two agents may share a name, so the seat that
                          distinguishes them travels with it. */}
                      {seat && ` · ${seat.hostName} slot ${seat.slotIndex}`}
                      {!seat && isAgent && mine.length > 0 && " · self-hosted"}
                    </span>
                  </span>
                </button>

                <span className="flex shrink-0 flex-wrap items-center gap-2">
                  {/* Live status: what an agent is doing right now, or what a
                      person has on their plate. */}
                  {claim ? (
                    <>
                      <StatusBadge status="running" />
                      <span className="flex min-w-0 flex-col items-end">
                        <Link
                          href={claim.run_id ? `/runs/${claim.run_id}` : "#"}
                          className="max-w-64 truncate text-xs font-medium hover:underline"
                        >
                          {claim.kind} · {claim.title}
                        </Link>
                        <span className="text-[11px] text-muted-foreground">
                          {claim.project ? `${claim.project} · ` : ""}
                          {elapsedSince(claim.claimed_at, now) ?? "running"}
                        </span>
                      </span>
                    </>
                  ) : isAgent ? (
                    <span className="flex min-w-0 flex-col items-end">
                      <span
                        className={cn(
                          "text-xs font-medium",
                          idleTone(idle?.reason ?? (status?.online ? "idle" : "unknown")),
                        )}
                      >
                        {status && !status.online
                          ? "Offline"
                          : (IDLE_LABELS[idle?.reason ?? "idle"] ?? "Idle")}
                      </span>
                      <span className="max-w-56 truncate text-[11px] text-muted-foreground">
                        {status && !status.online
                          ? `last seen ${formatLastSeen(lastSeen ?? null)}`
                          : revoked
                            ? "its token has been revoked"
                            : (idle?.detail ?? "")}
                      </span>
                    </span>
                  ) : assigned > 0 ? (
                    <Link
                      href="/issues"
                      className="text-xs text-muted-foreground hover:underline"
                    >
                      {assigned} open {assigned === 1 ? "item" : "items"}
                    </Link>
                  ) : (
                    <span className="text-xs text-muted-foreground">Nothing assigned</span>
                  )}

                  {/* US-61.1: role never gated an agent's own behavior — it
                      authenticates over a worker token, never auth.uid(), so
                      role-based capability checks are structurally
                      unreachable for it. A fixed, non-editable "Agent" badge
                      replaces the picker rather than offering a choice that
                      was always decorative. */}
                  {isAgent ? (
                    <span className="rounded-full border px-2.5 py-0.5 text-xs font-medium text-muted-foreground">
                      Agent
                    </span>
                  ) : canManage ? (
                    <Select
                      items={ROLES.map((r) => ({ value: r, label: ROLE_LABELS[r] }))}
                      value={m.role}
                      onValueChange={(v) => {
                        if (typeof v === "string" && v !== m.role)
                          mutate(m.principal_id, { role: v as Role });
                      }}
                      disabled={busyId === m.principal_id}
                    >
                      <SelectTrigger className="w-32">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {ROLES.map((r) => (
                          <SelectItem key={r} value={r}>
                            {ROLE_LABELS[r]}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  ) : (
                    <span className="rounded-full border px-2.5 py-0.5 text-xs font-medium text-muted-foreground">
                      {ROLE_LABELS[m.role] ?? m.role}
                    </span>
                  )}
                  {canManage && (
                    <>
                      <Button
                        variant="outline"
                        size="sm"
                        title={suspended ? "Reactivate" : "Suspend"}
                        disabled={busyId === m.principal_id}
                        onClick={() =>
                          mutate(m.principal_id, {
                            status: suspended ? "active" : "suspended",
                          })
                        }
                      >
                        {suspended ? <Play className="size-4" /> : <Pause className="size-4" />}
                      </Button>
                      {!isAgent && m.user_id && m.principals?.email && (
                        <Button
                          variant="outline"
                          size="sm"
                          title="Reset password"
                          disabled={busyId === m.principal_id}
                          onClick={() => resetPassword(m.user_id!, m.principals!.email!)}
                        >
                          <KeyRound className="size-4" />
                        </Button>
                      )}
                      <Button
                        variant="outline"
                        size="sm"
                        title="Remove"
                        disabled={busyId === m.principal_id}
                        onClick={() => remove(m.principal_id)}
                      >
                        {busyId === m.principal_id ? (
                          <Loader2 className="size-4 animate-spin" />
                        ) : (
                          <UserMinus className="size-4" />
                        )}
                      </Button>
                    </>
                  )}
                  {isAgent && (
                    <>
                      <Button
                        variant="outline"
                        size="sm"
                        title="Open runner console"
                        onClick={() => router.push(`/team/${m.principal_id}/runner`)}
                      >
                        <Cpu className="size-4" />
                      </Button>
                      {/* US-78.11: the interactive agent's CLI window, reachable
                          whether or not it is working. Every other way in
                          (Activity → a run) requires a run to exist, so an idle
                          agent had no door at all. Glows while it is working —
                          the same claim rows the "working on" line reads, so the
                          two can never disagree. */}
                      {(moduleByPrincipal[m.principal_id] ?? []).includes(
                        "interactive",
                      ) && (
                        <Button
                          variant="outline"
                          size="sm"
                          title={
                            runningRunByPrincipal[m.principal_id]
                              ? "Open CLI window — working now"
                              : "Open CLI window"
                          }
                          className={cn(
                            runningRunByPrincipal[m.principal_id] &&
                              "border-emerald-500 text-emerald-600 shadow-[0_0_0_2px_rgba(16,185,129,0.25)] animate-pulse dark:text-emerald-400",
                          )}
                          onClick={() =>
                            router.push(`/team/${m.principal_id}/console`)
                          }
                        >
                          <TerminalSquare className="size-4" />
                        </Button>
                      )}
                      {/* US-32.1: settings are a sibling page, not a section of
                          the console. */}
                      <Button
                        variant="outline"
                        size="sm"
                        title="Agent settings"
                        onClick={() => router.push(`/team/${m.principal_id}/settings`)}
                      >
                        <SlidersHorizontal className="size-4" />
                      </Button>
                      {/* US-55.6: same gate as the agent's own Machine section —
                          subscription-billed and manage_org only — but
                          available right from the row, no page visit first. */}
                      {canManageOrg &&
                        claudeBillingByPrincipal[m.principal_id] === "subscription" &&
                        hostByPrincipal[m.principal_id]?.serverId &&
                        hostByPrincipal[m.principal_id]?.slotId && (
                          <Button
                            variant="outline"
                            size="sm"
                            title="Claude Terminal"
                            onClick={() =>
                              openClaudeTerminal(
                                hostByPrincipal[m.principal_id].serverId as string,
                                hostByPrincipal[m.principal_id].slotId as string,
                              )
                            }
                          >
                            <SquareTerminal className="size-4" />
                          </Button>
                        )}
                    </>
                  )}
                  <Button
                    variant="outline"
                    size="sm"
                    title={expanded ? "Hide details" : "Show details"}
                    onClick={toggle}
                  >
                    <ChevronDown
                      className={cn("size-4 transition-transform", expanded && "rotate-180")}
                    />
                  </Button>
                </span>
              </div>

              {expanded && (
                <div className="border-t bg-muted/20 p-4">
                  <Tabs defaultValue="details">
                    <TabsList>
                      <TabsTrigger value="details">Details</TabsTrigger>
                      <TabsTrigger value="connect">Connect</TabsTrigger>
                    </TabsList>
                    <TabsContent value="details" className="pt-3">
                      <MemberDetail
                        orgId={orgId}
                        member={m}
                        workers={mine}
                        projects={projects}
                        slot={seat ?? null}
                        embedded
                      />
                    </TabsContent>
                    <TabsContent value="connect" className="grid gap-5 pt-3">
                      {/* US-63.x: the token card lives here, not on Details —
                          a router token is a connect credential, and burying
                          it a tab away from "how do I plug this in" never
                          made sense. */}
                      <RouterTokenPanel
                        orgId={orgId}
                        member={m}
                        workers={mine}
                        projects={projects}
                        canManageTokens={myPrincipalId === m.principal_id || canManage}
                        slot={seat ?? null}
                      />
                      <ConnectPanel
                        principals={principals.filter((p) => p.principalId === m.principal_id)}
                        projects={projects}
                        orgShortname={orgShortname}
                        initialPrincipalId={m.principal_id}
                      />
                    </TabsContent>
                  </Tabs>
                </div>
              )}
            </li>
          );
        })}
      </ul>

      {resetReveal && (
        <PasswordReveal email={resetReveal.email} password={resetReveal.password} />
      )}
      {error && <p className="text-sm font-medium text-destructive">{error}</p>}
    </div>
  );
}
