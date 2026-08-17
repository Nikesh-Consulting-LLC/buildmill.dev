"use client";

// US-10.12 → US-32.1: an agent's runner console, and only that. Reached from
// the Team drawer (a runner belongs to its agent, not a top-level menu). Shows,
// live, what the agent's runner is doing right now — presence, health, the
// current work item, a realtime feed of the commands it's running, and the
// runner-fault incidents behind it.
//
// The configuration form that used to share this scroll now lives on the
// sibling settings page. Watching an agent work and deciding how it should work
// are different tasks done in different states of mind, and the incident list —
// the thing a manager opens this page to read — should not share a scroll with
// a form that must not be half-submitted.

import { useEffect, useMemo, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";

import { apiCall } from "@/lib/api";
import { toastError, toastSuccess } from "@/components/ui/toast";
import {
  STATUS_LABELS,
  idleFixHref,
  stateFor,
  statusBadgeClass,
  type AgentStatus,
} from "@/lib/idle-reasons";
import { SpendSummary } from "@/components/spend-summary";

import { AgentTabs } from "../agent-tabs";
import { PrepareCodebaseButton } from "./prepare-codebase-button";
import {
  HEALTH_STYLES,
  healthFor,
  useAgentRunner,
  type AgentSlot,
  type Command,
  type CurrentRun,
  type Health,
  type Incident,
  type Session,
  type Worker,
} from "../agent-runner-data";

/** Where a settings section lives, for the in-context links below. */
function settingsHref(principalId: string, anchor: string) {
  return `/team/${principalId}/settings#${anchor}`;
}

type Fix = { href: string; label: string };

/** US-39.3: how long the current run has been going, in words. */
function minutesSince(iso: string): string {
  const mins = Math.floor((Date.now() - new Date(iso).getTime()) / 60000);
  if (mins < 1) return "just started";
  if (mins < 60) return `${mins}m`;
  const h = Math.floor(mins / 60);
  return `${h}h ${mins % 60}m`;
}

/**
 * US-32.1: a surface that names a problem offers the action that fixes it. The
 * runner reports faults as free text, so this matches on the phrases the
 * supervisor and API actually emit — and returns nothing rather than guessing.
 */
function fixForIncident(
  principalId: string,
  incident: Incident,
): Fix | null {
  const text = `${incident.kind ?? ""} ${incident.message ?? ""}`.toLowerCase();
  if (text.includes("no enabled module") || text.includes("not enabled")) {
    return {
      href: settingsHref(principalId, "modules"),
      label: "Enabled modules",
    };
  }
  // US-57.6/57.7: model routes, autonomy policy and the two run limits are
  // the platform's now — an org user has no page here that fixes them, so
  // this stops pointing at settings-page anchors that no longer exist.
  return null;
}

/** The same question for the idle-reason readout, whose reasons are a fixed
 *  set. us-116.2: the shared map (`idleFixHref`) carries the settings-page
 *  anchors for the configuration reasons; `paused` stays here because its fix
 *  is the machine, which only this page knows. */
function fixForIdle(
  principalId: string,
  reason: string,
  slot: AgentSlot | null,
): Fix | null {
  if (reason === "paused") {
    // US-35.2: the machine, not the retired agent-server page.
    return slot?.serverId
      ? { href: `/servers/${slot.serverId}`, label: "Agent controls" }
      : null;
  }
  return idleFixHref(principalId, reason);
}

export default function AgentRunnerPage() {
  const { principalId } = useParams<{ principalId: string }>();
  const {
    loading,
    name,
    workers,
    otherWorkers,
    slot,
    sessions,
    runs,
    commands,
    incidents,
    reload,
  } = useAgentRunner(principalId);
  const [fleetBusy, setFleetBusy] = useState(false);

  if (loading) {
    return <div className="p-1 text-sm text-muted-foreground">Loading runner console…</div>;
  }

  return (
    <div className="flex w-full flex-col gap-6">
      <div className="flex flex-wrap items-center gap-3">
        <Link href="/team" className="text-sm text-muted-foreground hover:underline">
          ← Team
        </Link>
        <span className="text-muted-foreground">/</span>
        <h1 className="text-xl font-semibold">{name} · runner</h1>
      </div>

      <AgentTabs principalId={principalId} active="console" />

      {/* US-26.10: where this agent actually runs, and the fleet controls for
          it, right where you are already looking at it. */}
      {slot ? (
        <div className="flex flex-wrap items-center gap-3 rounded-md border p-3 text-sm">
          <span>
            Runs on{" "}
            <Link
              href={slot.serverId ? `/servers/${slot.serverId}` : "/servers"}
              className="font-medium underline underline-offset-4"
            >
              {slot.hostName}
            </Link>{" "}
            · slot {slot.slotIndex}
          </span>
          <span className="ml-auto flex gap-2">
            {/* us-116.5: Start means start — enable, and restart the service
                if the agent is not live — and Stop is today's pause. Both are
                the agent's own endpoints, authorized on the slot's org (so a
                tenant on a platform pool can use them), and every refusal is a
                toast: these used to `catch {}` and look like nothing happened. */}
            <button
              type="button"
              disabled={fleetBusy}
              data-testid={slot.paused ? "agent-start" : "agent-stop"}
              onClick={async () => {
                setFleetBusy(true);
                const action = slot.paused ? "start" : "stop";
                try {
                  const res = await apiCall(`/api/v1/agents/${principalId}/${action}`, {
                    method: "POST",
                  });
                  toastSuccess(
                    action === "start"
                      ? res?.restarted
                        ? "Enabled — restarting its service"
                        : "Started"
                      : res?.finishing
                        ? `Stopped — finishing ${res.finishing} first`
                        : "Stopped",
                  );
                  await reload();
                } catch (e) {
                  toastError(
                    action === "start" ? "Could not start" : "Could not stop",
                    (e as Error).message,
                  );
                } finally {
                  setFleetBusy(false);
                }
              }}
              className="rounded-md border px-2 py-1 text-xs hover:bg-muted disabled:opacity-50"
            >
              {slot.paused ? "Start" : "Stop"}
            </button>
            <button
              type="button"
              disabled={fleetBusy}
              onClick={async () => {
                setFleetBusy(true);
                try {
                  await apiCall(
                    `/api/v1/agent-servers/${slot.hostId}/slots/${slot.id}/restart`,
                    { method: "POST" }
                  );
                  toastSuccess("Restart queued");
                } catch (e) {
                  toastError("Could not restart", (e as Error).message);
                } finally {
                  setFleetBusy(false);
                }
              }}
              className="rounded-md border px-2 py-1 text-xs hover:bg-muted disabled:opacity-50"
            >
              Restart
            </button>
          </span>
        </div>
      ) : (
        workers.length > 0 && (
          <p className="text-sm text-muted-foreground">
            This runner was installed outside Build Mill, so there are no
            start/stop controls for it here —{" "}
            <Link
              href={`/team/${principalId}/settings`}
              className="underline underline-offset-4"
            >
              its settings
            </Link>{" "}
            still apply.
          </p>
        )
      )}

      {workers.length === 0 ? (
        otherWorkers.length > 0 ? (
          <div className="rounded-md border border-dashed p-8 text-sm text-muted-foreground">
            <p className="font-medium text-foreground">
              This principal is a headless MCP worker — there is no runner
              console for it.
            </p>
            <p className="mt-2 max-w-2xl">
              It connects per-call over the factory MCP (no persistent socket,
              no shell the factory controls), so runner configuration does not
              apply. See what it is doing on{" "}
              <Link href="/team?tab=live" className="underline underline-offset-4">
                Team → Live
              </Link>{" "}
              — an active claim with a recent heartbeat is its sign of life.
            </p>
          </div>
        ) : (
          <div className="rounded-md border border-dashed p-8 text-center text-sm text-muted-foreground">
            This agent has no autonomous runner yet. Mint a token for it on the
            Team page and start one with <code>python -m supervisor</code>.
          </div>
        )
      ) : (
        workers.map((worker) => (
          <RunnerConsole
            key={worker.id}
            principalId={principalId}
            worker={worker}
            orgId={(worker as Worker & { org_id?: string }).org_id ?? null}
            slot={slot}
            online={sessions.some((s) => s.worker_id === worker.id)}
            session={sessions.find((s) => s.worker_id === worker.id) ?? null}
            health={healthFor(worker.id, incidents)}
            current={runs.find((r) => r.worker_id === worker.id) ?? null}
            commands={commands.filter((c) => c.worker_id === worker.id)}
            incidents={incidents.filter((i) => i.worker_id === worker.id)}
          />
        ))
      )}
    </div>
  );
}

function RunnerConsole({
  principalId,
  worker,
  orgId,
  slot,
  online,
  session,
  health,
  current,
  commands,
  incidents,
}: {
  principalId: string;
  worker: Worker;
  orgId: string | null;
  slot: AgentSlot | null;
  online: boolean;
  session: Session | null;
  health: Health;
  current: CurrentRun | null;
  commands: Command[];
  incidents: Incident[];
}) {
  const running = useMemo(
    () => commands.find((c) => c.finished_at === null),
    [commands],
  );
  const host = (session?.host_info as { hostname?: string } | null)?.hostname;
  // US-27.9: why this agent is not working, from the same resolver the host's
  // Agents tab uses. A connected socket proves the process is alive; it does
  // not prove the worker may claim.
  const [idle, setIdle] = useState<AgentStatus | null>(null);
  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const res = await apiCall(`/api/v1/runner/${worker.id}/idle-reason`);
        if (!cancelled) setIdle(res ?? null);
      } catch {
        // an explanation that cannot be fetched is not worth a broken page
      }
    }
    void load();
    const timer = setInterval(load, 20000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [worker.id]);

  const idleFix = idle
    ? fixForIdle(principalId, idle.reason ?? idle.state, slot)
    : null;
  // us-116.4: THE state — the same word the roster and the machine page show
  // for this agent. Presence from the live view (realtime), the rest from the
  // status poll above; `online · paused` is gone because "online" said nothing
  // about whether it could work.
  const state = stateFor(online, idle);

  return (
    <div className="rounded-lg border">
      <div className="flex flex-wrap items-center gap-3 border-b p-4">
        <span className="font-medium">{worker.name}</span>
        <span
          className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs ${statusBadgeClass(state)}`}
          data-testid="agent-state"
        >
          <span className={`h-1.5 w-1.5 rounded-full ${online ? "bg-emerald-500" : "bg-muted-foreground"}`} />
          {STATUS_LABELS[state]}
        </span>
        {/* US-13.8: "offline · healthy" was a contradiction — health only
            means something while connected or when faults exist. */}
        {(online || health !== "healthy") && (
          <span className={`rounded-full px-2 py-0.5 text-xs ${HEALTH_STYLES[health]}`}>{health}</span>
        )}
        {host && <span className="text-xs text-muted-foreground">{host}</span>}
        <Link
          href={`/team/${principalId}/settings`}
          className="ml-auto text-xs text-muted-foreground underline underline-offset-4 hover:text-foreground"
        >
          Settings
        </Link>
      </div>

      {/* US-39.3: the work leads, not the machine. The page answers, in this
          order: what item is it on, what is it doing right now, how long has it
          been doing it. Spend moved below — it is a fact about the agent, not
          an answer to "what is happening". */}
      <div className="border-b p-4">
        {current ? (
          <div className="text-sm">
            <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
              <span className="rounded bg-primary/10 px-1.5 py-0.5 text-xs text-primary">
                {current.kind}
              </span>
              {current.issueId ? (
                <Link
                  href={`/issues/${current.issueId}?from=${encodeURIComponent(`/team/${principalId}/runner`)}&fromLabel=${encodeURIComponent(worker.name)}`}
                  className="font-medium underline underline-offset-4"
                >
                  {current.title}
                </Link>
              ) : (
                <span className="font-medium">{current.title}</span>
              )}
              {current.startedAt && (
                <span className="text-xs text-muted-foreground">
                  · {minutesSince(current.startedAt)}
                </span>
              )}
            </div>
            {/* US-39.1 feeds this. Before it, the only clue to what an agent
                was doing was the command that launched it. */}
            <div className="mt-1 flex items-start gap-2 text-sm text-muted-foreground">
              <span className="mt-1.5 inline-block size-1.5 shrink-0 animate-pulse rounded-full bg-emerald-500" />
              <span className="min-w-0 break-words">
                {current.activity ?? "starting…"}
              </span>
            </div>
          </div>
        ) : (
          <div
            className={`text-sm ${
              idle?.reason === "revoked"
                ? "text-red-600 dark:text-red-400"
                : "text-muted-foreground"
            }`}
          >
            {/* US-27.9: the same line the host's Agents tab shows. "Waiting
                for work" must only ever mean there is no work. US-32.1: and
                where a setting is responsible, it links to it. */}
            {!online
              ? "Offline."
              : idle
                ? idle.detail
                : "Idle — waiting for work."}
            {online && idleFix && (
              <>
                {" — "}
                <Link
                  href={idleFix.href}
                  className="underline underline-offset-4 hover:text-foreground"
                >
                  {idleFix.label}
                </Link>
              </>
            )}
          </div>
        )}
        {/* The launch command carries the entire prompt — ~1,500 characters
            that dominated this page twice over. Available, not in the way. */}
        {running && (
          <details className="mt-2 text-xs">
            <summary className="cursor-pointer text-muted-foreground hover:text-foreground">
              Launch command
            </summary>
            <pre className="mt-1 max-h-40 w-0 min-w-full overflow-auto rounded border bg-muted/30 p-2 font-mono text-[11px] whitespace-pre-wrap break-all">
              {(running.argv ?? []).join(" ")}
            </pre>
          </details>
        )}
        {orgId && (
          <div className="mt-3 border-t pt-2">
            <SpendSummary orgId={orgId} workerId={worker.id} label="This agent" />
          </div>
        )}
      </div>

      {/* Prove the agent can actually reach the factory remote and get
          source onto disk — on demand, not just when a real run needs it. */}
      <div className="border-b p-4">
        <div className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
          Codebase check
        </div>
        <PrepareCodebaseButton workerId={worker.id} />
      </div>

      {/* Command feed */}
      <div className="border-b p-4">
        <div className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
          Command feed (live)
        </div>
        {commands.length === 0 ? (
          <p className="text-sm text-muted-foreground">No commands yet.</p>
        ) : (
          <div className="max-h-72 overflow-auto rounded-md border bg-muted/30">
            <table className="w-full text-left text-xs">
              <tbody>
                {commands.map((c) => (
                  <tr key={c.id} className="border-b last:border-0">
                    <td className="whitespace-nowrap py-1 pl-2 pr-3 text-muted-foreground">
                      {new Date(c.started_at).toLocaleTimeString()}
                    </td>
                    <td className="py-1 pr-3">
                      {c.policy_decision === "deny" ? (
                        <Link
                          href={settingsHref(principalId, "policy")}
                          className="text-red-600 underline underline-offset-4 dark:text-red-400"
                        >
                          deny
                        </Link>
                      ) : c.finished_at === null ? (
                        <span className="text-emerald-600 dark:text-emerald-400">running…</span>
                      ) : (
                        <span className="text-muted-foreground">exit {c.exit_code ?? "—"}</span>
                      )}
                    </td>
                    <td className="py-1 pr-2 font-mono">{(c.argv ?? []).join(" ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {incidents.length > 0 && (
        <div className="p-4">
          <div className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Runner-fault incidents
          </div>
          <ul className="space-y-1 text-xs">
            {incidents.slice(0, 5).map((i) => {
              const fix = fixForIncident(principalId, i);
              return (
                <li key={i.id} className="text-red-700 dark:text-red-300">
                  <span className="text-muted-foreground">{new Date(i.created_at).toLocaleString()}</span>{" "}
                  — {i.message ?? i.kind}
                  {fix && (
                    <>
                      {" — "}
                      <Link
                        href={fix.href}
                        className="underline underline-offset-4 hover:text-foreground"
                      >
                        {fix.label}
                      </Link>
                    </>
                  )}
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </div>
  );
}
