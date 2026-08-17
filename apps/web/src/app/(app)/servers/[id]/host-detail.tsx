"use client";

import { useEffect, useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import Link from "next/link";
import {
  Activity,
  CircleStop,
  Cpu,
  FolderTree,
  HardDrive,
  KeyRound,
  Loader2,
  MemoryStick,
  Play,
  Plus,
  RotateCcw,
  Settings2,
  Trash2,
  Users,
} from "lucide-react";
import { apiCall, ApiError } from "@/lib/api";
import { createClient } from "@/lib/supabase/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { AgentRename } from "@/components/agent-rename";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { toastError, toastSuccess } from "@/components/ui/toast";
import { cn } from "@/lib/utils";
import {
  STATUS_LABELS,
  stateFor,
  statusBadgeClass,
  type AgentStatus,
} from "@/lib/idle-reasons";
import type { AgentServerRow, SlotRow } from "../agent-host";
import { probeAge } from "../agent-host";

export type SlotWithAgent = SlotRow & {
  connected: boolean;
  paused: boolean;
  currentWork: string | null;
};

export type JobRow = {
  id: string;
  kind: string;
  status: "queued" | "running" | "succeeded" | "partial" | "failed";
  step: string | null;
  log: string;
  error: string | null;
  started_by_email: string;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
};

const MODULES = ["claude", "grok", "opencode"] as const;

const JOB_STATUS_STYLES: Record<string, string> = {
  queued: "bg-muted text-muted-foreground",
  running: "bg-blue-100 text-blue-700 dark:bg-blue-950 dark:text-blue-300",
  succeeded: "bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300",
  partial: "bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-300",
  failed: "bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-300",
};

export function HostDetail({
  host,
  slots,
  jobs,
  projects,
  currentBundleHash,
  owners,
}: {
  host: AgentServerRow;
  slots: SlotWithAgent[];
  jobs: JobRow[];
  projects: { id: string; name: string }[];
  currentBundleHash: string | null;
  /** US-57.19: slot id → owning org's name. Passed only by the superadmin
   *  machine page — on a tenant's own page every agent is theirs and the
   *  label would be noise. */
  owners?: Record<string, string>;
}) {
  const router = useRouter();
  const [busy, setBusy] = useState<string | null>(null);

  const running = jobs.find((j) => j.status === "running" || j.status === "queued");
  const drifted =
    !!currentBundleHash && !!host.bundle_hash && host.bundle_hash !== currentBundleHash;
  const probe = probeAge(host.last_probe_at);
  // US-31.8: the probe writes these; the generated types trail the migration
  // until they are regenerated, so read them through a narrow cast rather
  // than widening the Host type by hand.
  const hostWorkspace = host as typeof host & {
    workspace_bytes?: number | null;
    workspace_count?: number | null;
  };
  const hostWorkspaceBytes = hostWorkspace.workspace_bytes ?? null;
  const hostWorkspaceCount = hostWorkspace.workspace_count ?? null;

  // US-26.2: the job log streams as it is appended. Realtime on
  // agent_server_jobs / agent_slots / agent_servers, so the page follows the
  // machine without polling.
  useEffect(() => {
    const supabase = createClient();
    let channel: ReturnType<typeof supabase.channel> | null = null;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const refresh = () => {
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => router.refreshSilently(), 400);
    };

    (async () => {
      const {
        data: { session },
      } = await supabase.auth.getSession();
      if (!session || cancelled) return;
      await supabase.realtime.setAuth(session.access_token);
      channel = supabase
        .channel(`agent-server-${host.id}`, { config: { private: false } })
        .on(
          "postgres_changes",
          {
            event: "*",
            schema: "public",
            table: "agent_server_jobs",
            filter: `agent_server_id=eq.${host.id}`,
          },
          refresh
        )
        .on(
          "postgres_changes",
          {
            event: "*",
            schema: "public",
            table: "agent_slots",
            filter: `agent_server_id=eq.${host.id}`,
          },
          refresh
        )
        .on(
          "postgres_changes",
          { event: "UPDATE", schema: "public", table: "agent_servers", filter: `id=eq.${host.id}` },
          refresh
        )
        .subscribe();
    })();

    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
      if (channel) supabase.removeChannel(channel);
    };
  }, [host.id, router]);

  async function act(
    label: string,
    path: string,
    init?: RequestInit,
    onConflict?: (detail: unknown) => boolean
  ) {
    setBusy(label);
    try {
      await apiCall(path, init);
      toastSuccess(`${label} started`);
      router.refresh();
    } catch (e) {
      if (e instanceof ApiError && e.status === 409 && onConflict?.(e.detail)) {
        return;
      }
      toastError(
        `${label} failed`,
        e instanceof ApiError && typeof e.detail === "object" && e.detail && "message" in e.detail
          ? String((e.detail as { message: string }).message)
          : (e as Error).message
      );
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="flex flex-col gap-6">
      {/* US-35.2: no name, no host line, no Terminal/Files here. This used to
          be a page of its own and carried its own header; it is now a section
          of the machine page, which already names the machine and offers SSH
          and Files through `MachineActions`. Rendering either twice is the
          duplication this phase exists to remove. What stays are the actions
          that are about the AGENTS on the machine, not the machine itself. */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="flex items-center gap-2 text-lg font-semibold">
          <Cpu className="size-4 text-muted-foreground" />
          Coding agents
          <span className="text-sm font-normal text-muted-foreground">
            {host.workdir}
            {host.os_release ? ` · ${host.os_release}` : ""}
          </span>
        </h2>
        <div className="flex flex-wrap gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={!!busy || !!running}
            onClick={() => act("Health check", `/api/v1/agent-servers/${host.id}/probe`, { method: "POST" })}
          >
            {busy === "Health check" ? <Loader2 className="size-4 animate-spin" /> : <Activity className="size-4" />}
            Check now
          </Button>
          {host.status !== "new" && (
            <Button
              variant={drifted ? "default" : "outline"}
              size="sm"
              disabled={!!busy || !!running}
              onClick={() => act("Update", `/api/v1/agent-servers/${host.id}/update`, { method: "POST" })}
            >
              {busy === "Update" ? <Loader2 className="size-4 animate-spin" /> : <RotateCcw className="size-4" />}
              Update
            </Button>
          )}
          <ProvisionButton
            host={host}
            disabled={!!busy || !!running}
            onRun={(slotCount) =>
              act("Provision", `/api/v1/agent-servers/${host.id}/provision`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ slots: slotCount }),
              })
            }
          />
        </div>
      </div>

      {running && (
        <p className="flex items-center gap-2 text-sm text-blue-700 dark:text-blue-300">
          <Loader2 className="size-4 animate-spin" />
          {running.kind} in progress{running.step ? ` — ${running.step}` : ""}. Other actions
          are held until it finishes.
        </p>
      )}

      <Tabs defaultValue="overview">
        <TabsList>
          <TabsTrigger value="overview">Overview</TabsTrigger>
          <TabsTrigger value="agents">Agents ({slots.length})</TabsTrigger>
          <TabsTrigger value="setup">Setup</TabsTrigger>
          <TabsTrigger value="activity">Activity</TabsTrigger>
        </TabsList>

        <TabsContent value="overview">
          <div className="grid gap-4 sm:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Machine</CardTitle>
              </CardHeader>
              <CardContent className="grid gap-2 text-sm">
                <Row icon={Cpu} label="CPU" value={host.cpu_count ? `${host.cpu_count} cores${host.load_avg !== null ? ` · load ${host.load_avg}` : ""}` : "—"} />
                <Row
                  icon={MemoryStick}
                  label="Memory"
                  value={
                    host.mem_free_mb !== null && host.mem_total_mb !== null
                      ? `${Math.round(host.mem_free_mb / 1024)} of ${Math.round(host.mem_total_mb / 1024)} GB free`
                      : "—"
                  }
                />
                <Row
                  icon={HardDrive}
                  label="Disk"
                  value={
                    host.disk_free_gb !== null && host.disk_total_gb !== null
                      ? `${host.disk_free_gb} of ${host.disk_total_gb} GB free`
                      : "—"
                  }
                />
                {/* US-31.8: kept per-project workspaces hold dependencies on
                    purpose — show what that costs, so a full disk is never a
                    surprise. */}
                <Row
                  icon={FolderTree}
                  label="Workspaces"
                  value={
                    hostWorkspaceBytes !== null
                      ? `${(hostWorkspaceBytes / 1024 ** 3).toFixed(1)} GB` +
                        (hostWorkspaceCount !== null
                          ? ` across ${hostWorkspaceCount} project${
                              hostWorkspaceCount === 1 ? "" : "s"
                            }`
                          : "")
                      : "—"
                  }
                />
                {probe && (
                  <p className={cn("text-xs", probe.stale ? "text-amber-600 dark:text-amber-400" : "text-muted-foreground")}>
                    {probe.stale
                      ? `Last health check ${probe.label} — stale, so these numbers may have moved.`
                      : `Health checked ${probe.label}.`}
                  </p>
                )}
                {host.probe_error && (
                  <p className="text-xs text-amber-600 dark:text-amber-400">
                    Last check failed: {host.probe_error}
                  </p>
                )}
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="text-base">Agent code</CardTitle>
              </CardHeader>
              <CardContent className="grid gap-2 text-sm">
                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">Installed</span>
                  <span className="font-mono text-xs">{host.bundle_hash ?? "not installed"}</span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">Current</span>
                  <span className="font-mono text-xs">{currentBundleHash ?? "unknown"}</span>
                </div>
                {drifted ? (
                  <p className="text-xs text-amber-600 dark:text-amber-400">
                    This machine is running older agent code. Update re-pushes it and
                    restarts each agent one at a time, waiting for in-flight work first.
                  </p>
                ) : host.bundle_hash ? (
                  <p className="text-xs text-muted-foreground">Up to date.</p>
                ) : null}
                {Object.keys(host.cli_versions ?? {}).length > 0 && (
                  <div className="mt-1 grid gap-1">
                    {Object.entries(host.cli_versions ?? {}).map(([mod, ver]) => (
                      <div key={mod} className="flex items-center justify-between text-xs">
                        <span className="font-mono text-muted-foreground">{mod}</span>
                        <span className="font-mono">{ver}</span>
                      </div>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>

            {host.shared && <PoolCard host={host} onSaved={() => router.refresh()} />}
          </div>
        </TabsContent>

        <TabsContent value="agents">
          <AgentsTab
            host={host}
            slots={slots}
            busy={busy}
            blocked={!!running}
            act={act}
            owners={owners}
          />
        </TabsContent>

        <TabsContent value="setup">
          <SetupTab host={host} projects={projects} />
        </TabsContent>

        <TabsContent value="activity">
          <ActivityTab jobs={jobs} />
        </TabsContent>
      </Tabs>
    </div>
  );
}

function Row({
  icon: Icon,
  label,
  value,
}: {
  icon: typeof Cpu;
  label: string;
  value: string;
}) {
  return (
    <div className="flex items-center justify-between">
      <span className="flex items-center gap-1.5 text-muted-foreground">
        <Icon className="size-3.5" />
        {label}
      </span>
      <span>{value}</span>
    </div>
  );
}

/** US-57.1: a shared machine is a pool — name and capacity, editable only by
 * the platform admin (enforced server-side; this card renders for anyone who
 * can see the row, which on a shared host is the platform org alone). */
function PoolCard({
  host,
  onSaved,
}: {
  host: AgentServerRow;
  onSaved: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [poolName, setPoolName] = useState(host.pool_name ?? "");
  const [capacity, setCapacity] = useState(String(host.capacity ?? 0));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    setSaving(true);
    setError(null);
    try {
      await apiCall(`/api/v1/agent-servers/${host.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pool_name: poolName.trim(), capacity: Number(capacity) }),
      });
      toastSuccess("Pool updated");
      setEditing(false);
      onSaved();
    } catch (e) {
      setError(
        e instanceof ApiError && typeof e.detail === "string" ? e.detail : (e as Error).message
      );
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Pool</CardTitle>
      </CardHeader>
      <CardContent className="grid gap-2 text-sm">
        {editing ? (
          <>
            <div className="grid gap-2">
              <Label htmlFor="pool-name">Pool name</Label>
              <Input id="pool-name" value={poolName} onChange={(e) => setPoolName(e.target.value)} />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="pool-capacity">Capacity (agents)</Label>
              <Input
                id="pool-capacity"
                inputMode="numeric"
                value={capacity}
                onChange={(e) => setCapacity(e.target.value)}
              />
            </div>
            {error && <p className="text-xs text-red-600 dark:text-red-400">{error}</p>}
            <div className="flex justify-end gap-2">
              <Button variant="outline" size="sm" onClick={() => setEditing(false)} disabled={saving}>
                Cancel
              </Button>
              <Button size="sm" onClick={save} disabled={saving}>
                {saving && <Loader2 className="size-4 animate-spin" />}
                Save
              </Button>
            </div>
          </>
        ) : (
          <>
            <Row icon={Users} label="Name" value={host.pool_name ?? "—"} />
            <Row icon={Users} label="Capacity" value={String(host.capacity ?? 0)} />
            <div className="flex justify-end">
              <Button variant="outline" size="sm" onClick={() => setEditing(true)}>
                Rename / resize
              </Button>
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}

function ProvisionButton({
  host,
  disabled,
  onRun,
}: {
  host: AgentServerRow;
  disabled: boolean;
  onRun: (slots: number) => void;
}) {
  const [slots, setSlots] = useState("2");
  if (host.status !== "new" && host.status !== "error") return null;
  return (
    <div className="flex items-center gap-2">
      <Input
        aria-label="Agents to create"
        className="h-9 w-16"
        inputMode="numeric"
        value={slots}
        onChange={(e) => setSlots(e.target.value)}
      />
      <Button size="sm" disabled={disabled} onClick={() => onRun(Number(slots) || 0)}>
        {host.status === "error" ? "Resume provisioning" : "Provision"}
      </Button>
    </div>
  );
}

function AgentsTab({
  host,
  slots,
  busy,
  blocked,
  act,
  owners,
}: {
  host: AgentServerRow;
  slots: SlotWithAgent[];
  busy: string | null;
  blocked: boolean;
  act: (
    label: string,
    path: string,
    init?: RequestInit,
    onConflict?: (detail: unknown) => boolean
  ) => Promise<void>;
  owners?: Record<string, string>;
}) {
  const router = useRouter();
  const [adding, setAdding] = useState(false);
  // US-27.9: why each agent is idle. Presence is not permission — a connected
  // socket proves the process is alive, not that it may claim. On 2026-07-26
  // two revoked agents read as "waiting for work" here for fourteen minutes.
  const [reasons, setReasons] = useState<Record<string, AgentStatus>>({});

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const res = await apiCall(
          `/api/v1/agent-servers/${host.id}/slots/idle-reasons`
        );
        if (!cancelled) setReasons(res?.reasons ?? {});
      } catch {
        // a missing explanation is not worth breaking the page over
      }
    }
    void load();
    const timer = setInterval(load, 20000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [host.id]);

  async function addAgent(confirmCapacity = false) {
    setAdding(true);
    try {
      await apiCall(`/api/v1/agent-servers/${host.id}/slots`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ slots: 1, confirm_capacity: confirmCapacity }),
      });
      toastSuccess("Adding an agent");
      window.location.reload();
    } catch (e) {
      if (
        e instanceof ApiError &&
        e.status === 409 &&
        e.detail &&
        typeof e.detail === "object" &&
        "confirmable" in e.detail
      ) {
        const detail = e.detail as unknown as { message: string };
        if (window.confirm(`${detail.message}\n\nAdd the agent anyway?`)) {
          setAdding(false);
          return addAgent(true);
        }
      } else {
        toastError("Could not add an agent", (e as Error).message);
      }
    } finally {
      setAdding(false);
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted-foreground">
          Each agent is its own identity and its own service. New agents start
          paused — they connect, and claim nothing until you enable them.
        </p>
        <Button
          size="sm"
          variant="outline"
          disabled={adding || blocked || host.status === "new"}
          onClick={() => addAgent(false)}
        >
          {adding ? <Loader2 className="size-4 animate-spin" /> : <Plus className="size-4" />}
          Add agent
        </Button>
      </div>

      {slots.length === 0 ? (
        <Card>
          <CardContent className="py-8 text-center text-sm text-muted-foreground">
            No agents on this machine yet.
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-3">
          {slots.map((slot) => (
            <Card key={slot.id}>
              <CardContent className="flex flex-wrap items-center justify-between gap-3 py-4">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-medium">{slot.name}</span>
                    {/* US-32.2: the fleet card is where a manager notices the
                        generated name is wrong, so it is where the rename is.
                        The service badge beside it is what still tells two
                        same-named agents apart. */}
                    {slot.principal_id && (
                      <AgentRename
                        principalId={slot.principal_id}
                        name={slot.name}
                        compact
                        onRenamed={() => router.refresh()}
                      />
                    )}
                    <Badge variant="outline" className="font-mono text-[11px]">
                      {slot.service_name}
                    </Badge>
                    {/* US-57.19: on a shared pool the superadmin needs to know
                        whose agent this is before touching it. */}
                    {owners?.[slot.id] && (
                      <Badge
                        variant="secondary"
                        className="font-normal"
                        title="Owning organization"
                      >
                        {owners[slot.id]}
                      </Badge>
                    )}
                    {/* us-116.4: THE state — the same word the Team roster
                        shows for this agent, from the same status. Presence
                        is the live view (server-rendered), the rest is the
                        status poll; before it answers, the state falls back
                        to what this page already knows (offline / stopped /
                        ready). Paused/Enabled and Connected/Not connected
                        collapsed into it. */}
                    {(() => {
                      const state = stateFor(
                        slot.connected,
                        reasons[slot.id] ??
                          (slot.paused ? { state: "stopped" } : null),
                      );
                      return (
                        <Badge className={statusBadgeClass(state)} data-testid="agent-state">
                          {STATUS_LABELS[state]}
                        </Badge>
                      );
                    })()}
                    {/* Machine-only facts stay as secondary badges — they are
                        WHY, not the state. desired vs observed shown
                        separately, never merged. */}
                    {slot.service_state && slot.service_state !== "active" && (
                      <Badge className="bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-300">
                        service {slot.service_state}
                      </Badge>
                    )}
                    {/* US-68.3: the auto-repair ladder tried restart, reissue,
                        and update and none of them cleared this — it has
                        stopped retrying and is waiting on a human. */}
                    {slot.auto_repair_needs_attention && (
                      <Badge className="bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-300">
                        Auto-repair gave up
                      </Badge>
                    )}
                  </div>
                  <p
                    className={`mt-1 text-xs ${
                      reasons[slot.id]?.reason === "revoked"
                        ? "text-red-600 dark:text-red-400"
                        : "text-muted-foreground"
                    }`}
                  >
                    {/* US-27.9: say WHICH kind of idle this is. "Waiting for
                        work" must only ever mean there is no work. */}
                    {slot.currentWork
                      ? `Working on ${slot.currentWork}`
                      : reasons[slot.id]
                        ? reasons[slot.id].detail
                        : slot.paused
                          ? "Stopped — claiming nothing until started"
                          : "Ready"}
                    {slot.principal_id && (
                      <>
                        {" · "}
                        <Link
                          href={`/team/${slot.principal_id}/runner`}
                          className="underline hover:text-foreground"
                        >
                          console
                        </Link>
                        {" · "}
                        {/* US-32.1: settings are their own page now */}
                        <Link
                          href={`/team/${slot.principal_id}/settings`}
                          className="underline hover:text-foreground"
                        >
                          settings
                        </Link>
                      </>
                    )}
                  </p>
                </div>
                <div className="flex flex-wrap gap-2">
                  {/* us-116.5: Start means start — the agent's own endpoint,
                      authorized on the slot's org: enable, and restart the
                      service if it is not live. Stop is today's pause. */}
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={!!busy || !slot.principal_id}
                    data-testid={slot.paused ? "agent-start" : "agent-stop"}
                    onClick={() =>
                      act(
                        slot.paused ? "Start" : "Stop",
                        `/api/v1/agents/${slot.principal_id}/${slot.paused ? "start" : "stop"}`,
                        { method: "POST" }
                      )
                    }
                  >
                    {slot.paused ? <Play className="size-4" /> : <CircleStop className="size-4" />}
                    {slot.paused ? "Start" : "Stop"}
                  </Button>
                  {/* US-27.9: the repair. Un-revoking is not offered — a
                      revoked credential stays revoked; this delivers a new
                      one to the machine that needs it. */}
                  {reasons[slot.id]?.reason === "revoked" && (
                    <Button
                      size="sm"
                      disabled={!!busy}
                      onClick={() =>
                        act(
                          "Re-issuing the token",
                          `/api/v1/agent-servers/${host.id}/slots/${slot.id}/reissue-token`,
                          { method: "POST" }
                        )
                      }
                    >
                      <KeyRound className="size-4" />
                      Re-issue token
                    </Button>
                  )}
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={!!busy || blocked}
                    onClick={() =>
                      act("Restart", `/api/v1/agent-servers/${host.id}/slots/${slot.id}/restart`, {
                        method: "POST",
                      })
                    }
                  >
                    <RotateCcw className="size-4" />
                    Restart
                  </Button>
                  <RemoveAgentButton host={host} slot={slot} disabled={!!busy || blocked} act={act} />
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}

function RemoveAgentButton({
  host,
  slot,
  disabled,
  act,
}: {
  host: AgentServerRow;
  slot: SlotWithAgent;
  disabled: boolean;
  act: (
    label: string,
    path: string,
    init?: RequestInit,
    onConflict?: (detail: unknown) => boolean
  ) => Promise<void>;
}) {
  return (
    <ConfirmDialog
      title={`Remove ${slot.name}?`}
      description="The service is stopped and removed, its token is revoked, and the agent is retired — kept, so past runs still name it. The machine keeps its other agents."
      confirmLabel="Remove"
      onConfirm={() =>
        act(
          "Remove",
          `/api/v1/agent-servers/${host.id}/slots/${slot.id}`,
          { method: "DELETE" },
          (detail) => {
            const d = detail as { message?: string; forcible?: boolean } | null;
            if (d?.forcible && window.confirm(`${d.message}\n\nForce removal?`)) {
              void act("Remove", `/api/v1/agent-servers/${host.id}/slots/${slot.id}?force=true`, {
                method: "DELETE",
              });
              return true;
            }
            toastError("Could not remove the agent", d?.message ?? "It is still busy.");
            return true;
          }
        )
      }
      trigger={
        <Button size="sm" variant="outline" disabled={disabled}>
          <Trash2 className="size-4" />
        </Button>
      }
    />
  );
}

function SetupTab({
  host,
  projects,
}: {
  host: AgentServerRow;
  projects: { id: string; name: string }[];
}) {
  const router = useRouter();
  const [modules, setModules] = useState<string[]>(host.modules ?? []);
  const [extras, setExtras] = useState((host.extra_packages ?? []).join(" "));
  const [setupCommands, setSetupCommands] = useState(host.setup_commands ?? "");
  const [allowSudo, setAllowSudo] = useState(host.allow_agent_sudo);
  const [autoRepair, setAutoRepair] = useState(host.auto_repair_enabled);
  const [saving, setSaving] = useState(false);

  // US-55.1: a template entry is project access, not a per-kind grant — the
  // per-kind `capabilities` key inside each entry is legacy from the matrix
  // era (any row already meant "assigned to the project") and is dropped on
  // the next save.
  const template = (host.slot_template ?? {}) as {
    enabled_modules?: string[];
    capabilities?: { project_id: string }[];
  };
  const [templateModules, setTemplateModules] = useState<string[]>(
    template.enabled_modules ?? []
  );
  const [accessIds, setAccessIds] = useState<string[]>(() => [
    ...new Set((template.capabilities ?? []).map((g) => g.project_id)),
  ]);

  function toggleAccess(projectId: string) {
    setAccessIds((cur) =>
      cur.includes(projectId)
        ? cur.filter((id) => id !== projectId)
        : [...cur, projectId]
    );
  }

  async function save() {
    setSaving(true);
    try {
      await apiCall(`/api/v1/agent-servers/${host.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          modules,
          extra_packages: extras.split(/[\s,]+/).map((p) => p.trim()).filter(Boolean),
          setup_commands: setupCommands,
          allow_agent_sudo: allowSudo,
          auto_repair_enabled: autoRepair,
          slot_template: {
            ...template,
            enabled_modules: templateModules,
            capabilities: accessIds.map((project_id) => ({ project_id })),
          },
        }),
      });
      toastSuccess("Saved", "Applied the next time this machine is provisioned or updated.");
      router.refresh();
    } catch (e) {
      toastError("Could not save", (e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">What this machine installs</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-4">
          <div className="grid gap-2">
            <Label>Coding agent CLIs</Label>
            <div className="flex flex-wrap gap-4">
              {MODULES.map((m) => (
                <label key={m} className="flex items-center gap-2 text-sm">
                  <Checkbox
                    checked={modules.includes(m)}
                    onCheckedChange={() =>
                      setModules((cur) =>
                        cur.includes(m) ? cur.filter((x) => x !== m) : [...cur, m]
                      )
                    }
                  />
                  <span className="font-mono text-xs">{m}</span>
                </label>
              ))}
            </div>
            <p className="text-xs text-muted-foreground">
              Deselecting one stops offering it to new agents; it is not
              uninstalled from the machine.
            </p>
          </div>
          <div className="grid gap-2">
            <Label htmlFor="setup-extras">Extra packages</Label>
            <Input
              id="setup-extras"
              value={extras}
              onChange={(e) => setExtras(e.target.value)}
              placeholder="postgresql-client dotnet-sdk-8.0"
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="setup-commands">Setup commands</Label>
            <Textarea
              id="setup-commands"
              rows={3}
              className="font-mono text-xs"
              value={setupCommands}
              onChange={(e) => setSetupCommands(e.target.value)}
            />
            <p className="text-xs text-muted-foreground">
              Run as root, in order, exactly as written — and re-run on every
              update.
            </p>
          </div>
          <label className="flex items-start gap-2 text-sm">
            <Checkbox checked={allowSudo} onCheckedChange={(v) => setAllowSudo(v === true)} />
            <span>
              Let agents install packages themselves
              <span className="block text-xs text-muted-foreground">
                Off by default. On, the agent account gets passwordless sudo.
              </span>
            </span>
          </label>
          <label className="flex items-start gap-2 text-sm">
            <Checkbox checked={autoRepair} onCheckedChange={(v) => setAutoRepair(v === true)} />
            <span>
              Auto repair
              <span className="block text-xs text-muted-foreground">
                On by default. Every ~15 minutes, a slot the health probe
                finds not actually running gets an escalating fix on its own
                — restart, then re-issue its token, then a full Update —
                before giving up and flagging it for a human. Only a
                platform admin can turn this off.
              </span>
            </span>
          </label>
        </CardContent>
      </Card>

      <div className="flex flex-col gap-4">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <Settings2 className="size-4" />
              New agent template
            </CardTitle>
          </CardHeader>
          <CardContent className="grid gap-4">
            <div className="grid gap-2">
              <Label>Modules a new agent starts with</Label>
              <div className="flex flex-wrap gap-4">
                {MODULES.map((m) => (
                  <label key={m} className="flex items-center gap-2 text-sm">
                    <Checkbox
                      checked={templateModules.includes(m)}
                      onCheckedChange={() =>
                        setTemplateModules((cur) =>
                          cur.includes(m) ? cur.filter((x) => x !== m) : [...cur, m]
                        )
                      }
                    />
                    <span className="font-mono text-xs">{m}</span>
                  </label>
                ))}
              </div>
            </div>
            <div className="grid gap-2">
              <Label>Which projects a new agent may access</Label>
              {projects.length === 0 ? (
                <p className="text-xs text-muted-foreground">No projects yet.</p>
              ) : (
                <div className="grid gap-1.5">
                  {projects.map((project) => (
                    <label
                      key={project.id}
                      className="flex items-center gap-2 text-sm"
                    >
                      <Checkbox
                        checked={accessIds.includes(project.id)}
                        onCheckedChange={() => toggleAccess(project.id)}
                      />
                      <span className="text-xs font-medium">{project.name}</span>
                    </label>
                  ))}
                </div>
              )}
              {/* US-55.1: the gate is fail-closed (us-31.3) — zero access
                  rows means the agent can claim nothing, not "unrestricted",
                  which is what this line used to claim. What an agent DOES on
                  an accessible project is its own kind checkboxes. */}
              <p className="text-xs text-muted-foreground">
                {accessIds.length === 0
                  ? "Nothing selected — new agents start with no project access and can claim nothing until access is granted on their Team page."
                  : `New agents start with access to ${accessIds.length} project(s).`}
              </p>
            </div>
            <p className="text-xs text-muted-foreground">
              Editing this does not change agents that already exist.
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base text-red-600 dark:text-red-400">
              <Trash2 className="size-4" />
              Decommission
            </CardTitle>
          </CardHeader>
          <CardContent>
            <TeardownButton host={host} />
          </CardContent>
        </Card>
      </div>

      <div className="lg:col-span-2">
        <Button onClick={save} disabled={saving}>
          {saving && <Loader2 className="size-4 animate-spin" />}
          Save setup
        </Button>
      </div>
    </div>
  );
}

function TeardownButton({ host }: { host: AgentServerRow }) {
  const router = useRouter();
  const [wipe, setWipe] = useState(false);

  return (
    <div className="grid gap-3">
      <label className="flex items-start gap-2 text-sm">
        <Checkbox checked={wipe} onCheckedChange={(v) => setWipe(v === true)} />
        <span>
          Also wipe {host.workdir}
          <span className="block text-xs text-muted-foreground">
            Off by default — an agent workspace can hold the only copy of an
            uncommitted diff.
          </span>
        </span>
      </label>
      <ConfirmDialog
        title="Tear down the agents on this machine?"
        description="Every agent is stopped and removed, its token revoked, and the systemd units deleted from the machine. The server registration itself, its credentials and its terminal are untouched."
        confirmLabel="Tear down"
        onConfirm={async () => {
          try {
            await apiCall(`/api/v1/agent-servers/${host.id}/teardown`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ wipe_workdir: wipe }),
            });
            toastSuccess("Teardown started");
            router.refresh();
          } catch (e) {
            toastError("Teardown failed", (e as Error).message);
          }
        }}
        trigger={
          <Button variant="outline" size="sm" className="w-fit">
            <Trash2 className="size-4" />
            Tear down
          </Button>
        }
      />
    </div>
  );
}

function ActivityTab({ jobs }: { jobs: JobRow[] }) {
  const [openId, setOpenId] = useState<string | null>(jobs[0]?.id ?? null);

  if (jobs.length === 0) {
    return (
      <Card>
        <CardContent className="py-8 text-center text-sm text-muted-foreground">
          Nothing has been done to this machine yet.
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="grid gap-3">
      {jobs.map((job) => {
        const open = openId === job.id;
        return (
          <Card key={job.id}>
            <CardHeader
              className="cursor-pointer flex-row items-center justify-between gap-3 space-y-0"
              onClick={() => setOpenId(open ? null : job.id)}
            >
              <CardTitle className="flex items-center gap-2 text-sm font-medium">
                <Users className="size-4 text-muted-foreground" />
                {job.kind}
                {job.step && job.status !== "succeeded" && (
                  <span className="text-xs text-muted-foreground">· {job.step}</span>
                )}
              </CardTitle>
              <div className="flex items-center gap-2">
                <span className="text-xs text-muted-foreground">
                  {new Date(job.created_at).toLocaleString()}
                  {job.started_by_email ? ` · ${job.started_by_email}` : ""}
                </span>
                <Badge className={JOB_STATUS_STYLES[job.status]}>{job.status}</Badge>
              </div>
            </CardHeader>
            {open && (
              <CardContent>
                {job.error && (
                  <p className="mb-2 text-sm text-red-600 dark:text-red-400">{job.error}</p>
                )}
                <pre className="max-h-96 overflow-auto rounded-md bg-muted p-3 font-mono text-xs whitespace-pre-wrap">
                  {job.log || "(no output yet)"}
                </pre>
              </CardContent>
            )}
          </Card>
        );
      })}
    </div>
  );
}
