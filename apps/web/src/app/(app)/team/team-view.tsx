"use client";

import { Fragment, useEffect, useMemo, useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import Link from "next/link";
import {
  Bot,
  ChevronDown,
  TerminalSquare,
  KeyRound,
  Pause,
  Play,
  Square,
  SlidersHorizontal,
  SquareTerminal,
  User,
  Users,
} from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import { RoleCapabilities } from "@/components/role-icon";
import { apiCall, apiFetch } from "@/lib/api";
import { toastError, toastSuccess } from "@/components/ui/toast";
import { cn } from "@/lib/utils";
import { formatWorkSeconds } from "@/lib/work-seconds";
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
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
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
import { MemberHistory } from "./[principalId]/member-history";
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
export type IdleReason = { reason: string; detail?: string | null; state?: string; last_seen_at?: string | null };

/** US-35.1: "Idle" alone was the whole problem — presence is not permission.
 *  us-116.2: the vocabulary and its tone live in `@/lib/idle-reasons` (a pure
 *  module the web tests can pin); re-exported here for the member page
 *  (US-53.3), which absorbed the drawer that used to render them. */
import {
  IDLE_LABELS,
  STATUS_LABELS,
  idleTone,
  stateFor,
  statusBadgeClass,
  statusTextClass,
} from "@/lib/idle-reasons";
export { IDLE_LABELS, idleTone };

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

export function formatWhen(iso: string) {
  return new Date(iso).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

export function principalName(m: MemberRow) {
  return m.principals?.display_name || m.principals?.email || m.principal_id;
}

/** US-91.12: one agent's totals over the KPI window. Seconds are what is
 *  stored (us-91.11); hours are a rendering. */
export type AgentEffort = {
  workSeconds: number;
  issuesCompleted: number;
  linesAdded: number;
  linesRemoved: number;
  tokens: number;
  costUsd: number;
};

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
  kindsByPrincipal,
  maxAgents,
  claudeBillingByPrincipal,
  moduleByPrincipal,
  runningRunByPrincipal,
  effortByPrincipal,
  effortWindowDays,
  canManageOrg,
  myPrincipalId,
  initialExpandedId,
  renderedAt = 0,
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
  /** us-107.3: `runner_config.enabled_kinds` per agent principal, so the row
   *  shows what it can do without opening it. Absent for people and headless
   *  MCP workers, which have no runner config at all. */
  kindsByPrincipal: Record<string, string[] | null>;
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
  /** US-91.12: each agent's totals over the reporting window. */
  effortByPrincipal: Record<string, AgentEffort>;
  /** The window those totals cover, so the expanded panel can label them. */
  effortWindowDays: number;
  /** US-55.6: gates the Claude Terminal action, same as the agent's own page. */
  canManageOrg: boolean;
  /** The signed-in user's own principal id, for "your own token" wording. */
  myPrincipalId: string | null;
  /** A deep link (`/team/{id}`, `?principal=`) lands here — expand that row. */
  initialExpandedId: string | null;
  /** us-116.4: the server render's timestamp. Changes on every refresh the
   *  realtime subscriptions trigger, so the status poll below re-runs with
   *  presence — the roster used to fetch reasons once and never again. */
  renderedAt?: number;
}) {
  const router = useRouter();

  // US-35.1 → us-116.4: each agent's status — presence in front of the idle
  // reason, from the org-scoped read the fleet page's host-scoped route
  // shares. `state` is what the State cell renders; `reason` feeds the fix
  // link. Re-fetched on every refresh and every 30 s.
  const [idleReasons, setIdleReasons] = useState<Record<string, IdleReason>>({});
  useEffect(() => {
    let cancelled = false;
    const load = () =>
      apiFetch(`/api/v1/agents/idle-reasons?org=${orgId}`)
        .then((d) => {
          if (!cancelled) setIdleReasons(d?.by_principal ?? {});
        })
        // Best-effort: an agent's reason is context, and failing to fetch it must
        // not take the roster down with it.
        .catch(() => {});
    void load();
    const timer = setInterval(load, 30000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [orgId, renderedAt]);

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
        kindsByPrincipal={kindsByPrincipal}
        moduleByPrincipal={moduleByPrincipal}
        runningRunByPrincipal={runningRunByPrincipal}
        effortByPrincipal={effortByPrincipal}
        effortWindowDays={effortWindowDays}
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
  kindsByPrincipal,
  moduleByPrincipal,
  runningRunByPrincipal,
  effortByPrincipal,
  effortWindowDays,
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
  /** us-107.3: `runner_config.enabled_kinds` per agent principal, so the row
   *  shows what it can do without opening it. Absent for people and headless
   *  MCP workers, which have no runner config at all. */
  kindsByPrincipal: Record<string, string[] | null>;
  moduleByPrincipal: Record<string, string[]>;
  /** US-78.11: principal -> the run it is holding right now, or absent.
   *  Drives the CLI button's glow. */
  runningRunByPrincipal: Record<string, string>;
  /** US-91.12: each agent's totals over the KPI window. */
  effortByPrincipal: Record<string, AgentEffort>;
  /** us-109.1: the window those totals cover, so the expanded panel can label
   *  them instead of showing five unattributed numbers. */
  effortWindowDays: number;
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

  // us-116.5: Start means start. One call per agent, authorized on its slot's
  // org; the API says what it did (enabled, and restarted the service when the
  // agent was not live) or why it refused — and every answer is a toast, never
  // swallowed. `router.refresh()` re-reads presence; the status poll follows.
  async function agentAction(principalId: string, action: "start" | "stop", name: string) {
    setError(null);
    setBusyId(principalId);
    try {
      const res = await apiCall(`/api/v1/agents/${principalId}/${action}`, {
        method: "POST",
      });
      if (action === "start") {
        toastSuccess(
          res?.restarted
            ? `${name} enabled — restarting its service`
            : `${name} started`,
        );
      } else {
        toastSuccess(
          res?.finishing
            ? `${name} stopped — finishing ${res.finishing} first`
            : `${name} stopped`,
        );
      }
      router.refresh();
    } catch (e) {
      toastError(
        action === "start" ? `Could not start ${name}` : `Could not stop ${name}`,
        (e as Error).message,
      );
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

  /** us-112.1: every cell's contents, computed once per member.
   *
   *  The roster renders in two shells — a table at `sm` and up, and the
   *  stacked card below it. US-68.6 introduced that card to fix the name
   *  being the only thing willing to give up width against a cluster of
   *  never-shrinking buttons, and a table at phone width would recreate
   *  exactly that. Two shells, but ONE source for what goes in them: a second
   *  copy of this logic is how the two would drift apart. */
  function cellsFor(m: MemberRow) {
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
    const moduleLabel =
      MODULES.find((mod) => mod.key === moduleKeys[0])?.label ?? null;
    const effort = effortByPrincipal[m.principal_id] ?? null;
    const expanded = expandedId === m.principal_id;
    const toggle = () =>
      setExpandedId((cur) => (cur === m.principal_id ? null : m.principal_id));

    // US-32.2: two agents may share a name, so the seat that distinguishes
    // them gets a column of its own rather than being a suffix on a subline
    // that started at a different x-position on every row.
    const seatText = seat
      ? `${seat.hostName} slot ${seat.slotIndex}`
      : isAgent && mine.length > 0
        ? "self-hosted"
        : null;

    const identity = (
      <button
        type="button"
        onClick={toggle}
        // w-full, or the button sizes to its content and overflows the cell
        // before the name ever truncates — which clips the badge after it.
        className="flex w-full min-w-0 items-center gap-2 text-left hover:opacity-80"
      >
        {isAgent ? (
          <Bot
            className={cn(
              "size-4 shrink-0",
              // Busy work is the whole point of an orange agent — a glance at
              // the icon should say so before reading any text.
              claim
                ? "text-orange-500 dark:text-orange-400"
                : "text-muted-foreground",
            )}
          />
        ) : (
          <User className="size-4 shrink-0 text-muted-foreground" />
        )}
        <span className="flex w-full min-w-0 flex-col">
          {/* min-w-0 at EVERY level of the flex chain, or `truncate` below
              never engages and a long agent name draws straight over the
              Does column instead of being cut. */}
          <span className="flex min-w-0 items-center gap-1.5">
            <span className="truncate font-medium">{principalName(m)}</span>
            {isAgent && (
              <span className="shrink-0 rounded-full border px-1.5 py-0.5 text-[10px] font-medium uppercase text-muted-foreground">
                agent
              </span>
            )}
            {suspended && (
              <span className="shrink-0 rounded-full border border-amber-500/50 px-1.5 py-0.5 text-[10px] font-medium uppercase text-amber-600 dark:text-amber-400">
                suspended
              </span>
            )}
          </span>
          {/* A person has no seat, so their address rides under the name
              rather than leaving the Seat column carrying an email. */}
          {!isAgent && m.principals?.email && (
            <span className="truncate text-xs text-muted-foreground">
              {m.principals.email}
            </span>
          )}
        </span>
      </button>
    );

    const does = isAgent ? (
      <span className="flex items-center gap-1">
{/* us-107.3: all four capabilities, greyed where the
            agent lacks one. Always four — "what can it not do"
            is usually the thing being looked for, and a filtered
            list silently answers only half the question. */}
        {isAgent && (
          <RoleCapabilities
            kinds={kindsByPrincipal[m.principal_id]}
            iconClassName="size-3.5"
          />
        )}
      </span>
    ) : null;

    // us-116.4: THE state — presence (the live view, realtime-fresh) in front
    // of the API's answer, one label map. The old online/offline pill collapsed
    // into this: "online" alone told the manager nothing about whether the
    // agent could work, and "Paused" on the machine page then read as a
    // contradiction.
    const agentState =
      isAgent && status
        ? stateFor(status.online, idle ? { ...idle, state: idle.state ?? idle.reason } : null)
        : null;

    const presence =
      isAgent && status ? (
        <span className="flex items-center gap-1.5">
{isAgent && status && (
        <>
          <span
            className={cn(
              "inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[10px] font-medium",
              statusBadgeClass(agentState ?? "ready"),
            )}
            data-testid="agent-state"
          >
            <span
              className={cn(
                "h-1.5 w-1.5 rounded-full",
                status.online ? "bg-emerald-500" : "bg-muted-foreground",
              )}
            />
            {STATUS_LABELS[agentState ?? "ready"]}
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
        </span>
      ) : null;

    const stateCell = (
      <span className="flex flex-wrap items-center gap-2">
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
                statusTextClass(agentState ?? "ready"),
              )}
            >
              {STATUS_LABELS[agentState ?? "ready"]}
            </span>
            <span className="max-w-56 truncate text-[11px] text-muted-foreground">
              {agentState === "offline"
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
      </span>
    );

    const roleCell = (
      <span className="flex items-center">
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
      </span>
    );

    // us-116.5: is this agent Stopped (paused) right now — the one fact the
    // Start/Stop toggle needs, from the same status the State cell renders.
    const agentStopped = agentState === "stopped";
    const actions = (
      <span className="flex shrink-0 items-center justify-end gap-1">
{canManage && (
          <>
            {/* us-116.5: the membership ▶/⏸ leaves agent rows. On an agent it
                was Suspend/Reactivate — and Suspend REVOKES the token, which
                is how the whole Sandy fleet died on 2026-08-09 from a button
                that looked like pause. Humans keep it; an agent's suspend
                lives in its detail panel behind a confirm that says so. */}
            {!isAgent && (
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
            )}
            {isAgent && canManageOrg && seat && (
              <Button
                variant="outline"
                size="sm"
                title={agentStopped ? "Start" : "Stop"}
                data-testid={agentStopped ? "agent-start" : "agent-stop"}
                disabled={busyId === m.principal_id}
                onClick={() =>
                  agentAction(
                    m.principal_id,
                    agentStopped ? "start" : "stop",
                    principalName(m),
                  )
                }
              >
                {agentStopped ? <Play className="size-4" /> : <Square className="size-4" />}
              </Button>
            )}
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
            {/* us-109.1: Remove is gone from this cluster. It was
                the one irreversible action here, wearing the same
                outline button as Suspend and sitting next to it —
                an agent's now lives on its settings page, a
                person's inside their expand panel. */}
          </>
        )}
        {isAgent && (
          <>
            {/* us-109.1: the runner console button is gone too. The
                console is still a page (`/team/{id}/runner`) and
                the Console tab on the settings page reaches it —
                it did not need a second door on every row. */}
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
    );

    const detail = (
      <>
        {/* The caller renders this only when `expanded`, so the guard that
            used to wrap it here would just be the same test twice.
            bg-muted/50, not /20: at /20 the panel was so close to the page
            background that it read as more page rather than as this row's
            detail. The expanded row above it carries the same tint, so the
            two form one block. */}
        <div className="border-t bg-muted/50 p-4">
          <Tabs defaultValue="details">
            <TabsList>
              <TabsTrigger value="details">Details</TabsTrigger>
              <TabsTrigger value="connect">Connect</TabsTrigger>
              {/* us-112.2: what this member has done, and how well — one
                  question, so one tab, instead of two sections buried at the
                  bottom of the longest one. */}
              <TabsTrigger value="history">History</TabsTrigger>
            </TabsList>
            <TabsContent value="details" className="pt-3">
              <MemberDetail
                orgId={orgId}
                member={m}
                workers={mine}
                projects={projects}
                slot={seat ?? null}
                embedded
                // us-109.1: what the row stopped showing.
                moduleLabel={moduleLabel}
                tokenCount={tokenCount}
                effort={effortByPrincipal[m.principal_id] ?? null}
                effortWindowDays={effortWindowDays}
                canManage={canManage}
                onRemoved={() => router.refresh()}
                // us-116.5: an agent's Suspend/Reactivate lives here now.
                onSuspendToggled={
                  isAgent
                    ? () =>
                        mutate(m.principal_id, {
                          status: suspended ? "active" : "suspended",
                        })
                    : undefined
                }
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
            <TabsContent value="history" className="pt-3">
              <MemberHistory orgId={orgId} member={m} workers={mine} />
            </TabsContent>
          </Tabs>
        </div>
      </>
    );

    return {
      isAgent,
      expanded,
      identity,
      does,
      presence,
      seatText,
      // us-112.1: two right-aligned numeric columns, not one prose string.
      // "4h 52m worked · 6 completed" cannot be compared down a list, and
      // comparing agents is the whole reason those figures are on the row.
      worked: effort ? formatWorkSeconds(effort.workSeconds) : null,
      done: effort ? String(effort.issuesCompleted) : null,
      stateCell,
      roleCell,
      actions,
      detail,
    };
  }

  const COLUMNS = [
    "Member",
    "Does",
    "Status",
    "Seat",
    "Worked",
    "Done",
    "State",
    "Role",
    "",
  ];

  return (
    <div className="grid min-w-0 gap-4">
      {/* ---- sm and up: a real table, so every field can be read down a
           column instead of hunted at a different x-position per row. ---- */}
      <Table className="hidden sm:table">
        <TableHeader>
          <TableRow>
            {COLUMNS.map((c, i) => (
              <TableHead
                key={c || `actions-${i}`}
                className={cn(
                  "whitespace-nowrap",
                  (c === "Worked" || c === "Done") && "text-right",
                )}
              >
                {c}
              </TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {members.map((m) => {
            const c = cellsFor(m);
            return (
              <Fragment key={m.principal_id}>
                <TableRow
                  id={`member-${m.principal_id}`}
                  // Expanded row and its panel share a tint so they read as
                  // one group rather than as a row and a floating slab.
                  className={cn(c.expanded && "bg-muted/50 hover:bg-muted/50")}
                >
                  {/* `w-full max-w-0` is the elastic-cell trick: this column
                      takes the leftover width and is the one that gives it
                      back, while every other cell is whitespace-nowrap.
                      overflow-hidden is the backstop — without it a long name
                      overflows the cell and paints across its neighbours. */}
                  <TableCell className="w-full max-w-0 overflow-hidden">
                    {c.identity}
                  </TableCell>
                  <TableCell className="whitespace-nowrap">{c.does}</TableCell>
                  <TableCell className="whitespace-nowrap">{c.presence}</TableCell>
                  <TableCell className="whitespace-nowrap text-xs text-muted-foreground">
                    {c.seatText}
                  </TableCell>
                  {/* A person shows nothing in these two rather than a line of
                      zeroes — us-91.11 AC6: user_activity_sessions measures
                      something else and must not be rendered as if it were
                      the same measurement. */}
                  <TableCell className="whitespace-nowrap text-right tabular-nums">
                    {c.worked}
                  </TableCell>
                  <TableCell className="whitespace-nowrap text-right tabular-nums">
                    {c.done}
                  </TableCell>
                  <TableCell className="whitespace-nowrap">{c.stateCell}</TableCell>
                  <TableCell className="whitespace-nowrap">{c.roleCell}</TableCell>
                  <TableCell className="whitespace-nowrap">{c.actions}</TableCell>
                </TableRow>
                {c.expanded && (
                  <TableRow className="hover:bg-transparent">
                    <TableCell colSpan={COLUMNS.length} className="p-0">
                      {c.detail}
                    </TableCell>
                  </TableRow>
                )}
              </Fragment>
            );
          })}
        </TableBody>
      </Table>

      {/* ---- below sm: the US-68.6 stacked card, same cell contents ----
           min-w-0 is load-bearing: a grid item's min-width defaults to
           min-content, and the flex-wrap rows inside report their
           single-line width as max-content — so without it the list asks
           for ~536px inside a 375px phone and the right-hand buttons are
           clipped off the screen with no way to scroll to them. */}
      <ul className="grid min-w-0 gap-2 sm:hidden">
        {members.map((m) => {
          const c = cellsFor(m);
          return (
            <li
              key={m.principal_id}
              id={`member-sm-${m.principal_id}`}
              // min-w-0 again: the li is itself a grid item, so it inherits
              // the same min-content default the ul did.
              className="min-w-0 rounded-md border"
            >
              <div className="flex min-w-0 flex-col gap-3 px-3 py-2 text-sm">
                <span className="flex flex-wrap items-center gap-1.5">
                  {c.identity}
                  {c.does}
                  {c.presence}
                </span>
                {c.seatText && (
                  <span className="text-xs text-muted-foreground">{c.seatText}</span>
                )}
                {c.worked && (
                  <span className="text-[11px] text-muted-foreground">
                    {c.worked} worked · {c.done} completed
                  </span>
                )}
                {c.stateCell}
                <span className="flex flex-wrap items-center gap-2">
                  {c.roleCell}
                  {c.actions}
                </span>
              </div>
              {c.expanded && c.detail}
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
