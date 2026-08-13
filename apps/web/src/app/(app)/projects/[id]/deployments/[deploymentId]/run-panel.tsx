"use client";

import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { useRouter } from "@/lib/router-with-progress";
import { Download, History, OctagonX, RotateCcw, Undo2 } from "lucide-react";
import { API_URL, apiCall, getAccessToken } from "@/lib/api";
import { createClient } from "@/lib/supabase/client";
import { cn } from "@/lib/utils";
import { StatusBadge, type IssueStatus } from "@/components/status-badge";
import { EmptyState } from "@/components/empty-state";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { AgentDispatchButton } from "../agent-dispatch-button";
import { RunDeploymentDialog } from "./run-deployment-dialog";
import { ZipDeployDialog, type StagedZip } from "./zip-deploy-dialog";
import { RefOverrideDialog } from "./ref-override-dialog";
import { PromoteDialog, type SiblingDeployment } from "./promote-dialog";

export type RunRow = {
  id: string;
  status: string;
  source: string;
  kind: string;
  branch: string | null;
  commit_sha: string | null;
  zip_filename: string | null;
  is_override: boolean;
  release_path: string | null;
  /** US-50.2: what an external run merged, and the PR it went through. */
  merge_commit_sha: string | null;
  pr_number: number | null;
  artifact_path: string | null;
  artifact_bytes: number | null;
  artifact_sha256: string | null;
  redeploy_of_run_id: string | null;
  promoted_from_run_id: string | null;
  started_by_email: string;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
};

type RunEvent = {
  id: number;
  run_id: string;
  phase: string;
  message: string;
  created_at: string;
};

const PHASE_STYLES: Record<string, string> = {
  preflight:
    "bg-cyan-100 text-cyan-700 dark:bg-cyan-950 dark:text-cyan-300",
  verify:
    "bg-pink-100 text-pink-700 dark:bg-pink-950 dark:text-pink-300",
  release:
    "bg-indigo-100 text-indigo-700 dark:bg-indigo-950 dark:text-indigo-300",
  fetch:
    "bg-blue-100 text-blue-700 dark:bg-blue-950 dark:text-blue-300",
  transfer:
    "bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-300",
  extract:
    "bg-violet-100 text-violet-700 dark:bg-violet-950 dark:text-violet-300",
  script: "bg-muted text-muted-foreground",
  done:
    "bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300",
  error: "bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-300",
};

function fmtTime(iso: string) {
  return new Date(iso).toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function fmtWhen(iso: string) {
  return new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function fmtDuration(startIso: string | null, endIso: string | null) {
  if (!startIso || !endIso) return null;
  const s = Math.max(0, Math.round((+new Date(endIso) - +new Date(startIso)) / 1000));
  return s >= 60 ? `${Math.floor(s / 60)}m ${s % 60}s` : `${s}s`;
}

export function RunPanel({
  deployment,
  serverLabel,
  stagedZip,
  initialRuns,
  isOwner,
  configChangedAreas,
  repoFullName,
  projectId,
  siblings,
  archiveTotalBytes,
}: {
  deployment: {
    id: string;
    name: string;
    kind: string;
    branch: string;
    target_branch: string;
    target_folder: string;
    strategy: string;
    source_folder: string;
    protected: boolean;
    environment: string | null;
    agent_dispatch_allowed: boolean;
  };
  serverLabel: string;
  stagedZip: StagedZip;
  initialRuns: RunRow[];
  isOwner: boolean;
  configChangedAreas: string[];
  repoFullName: string;
  projectId: string;
  siblings: SiblingDeployment[];
  archiveTotalBytes: number;
}) {
  const memberBlocked = deployment.protected && !isOwner;
  // US-50.2/50.3: an external run merges and stops. Nothing here that reaches
  // a machine applies to it, and calling the endpoints anyway is refused
  // server-side — these are the courtesy half.
  const external = deployment.kind === "external";

  async function downloadArtifact(run: RunRow) {
    // The bucket is never client-readable — stream through api with auth.
    const token = await getAccessToken();
    const resp = await fetch(
      `${API_URL}/api/v1/deployments/${deployment.id}/runs/${run.id}/artifact`,
      { headers: { Authorization: `Bearer ${token}` } }
    );
    if (!resp.ok) return;
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = run.artifact_path?.split("/").pop() ?? "artifact";
    a.click();
    URL.revokeObjectURL(url);
  }
  const router = useRouter();
  // US-2.16: a notification's deep link (?run=<id>) opens that run's log.
  const searchParams = useSearchParams();
  const deepLinkedRun = searchParams.get("run");
  const [selectedId, setSelectedId] = useState<string | null>(
    (deepLinkedRun && initialRuns.some((r) => r.id === deepLinkedRun)
      ? deepLinkedRun
      : initialRuns[0]?.id) ?? null
  );
  const [runs, setRuns] = useState<RunRow[]>(initialRuns);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const feedRef = useRef<HTMLDivElement>(null);

  const activeRun = runs.find((r) => r.status === "queued" || r.status === "running");
  const selected = runs.find((r) => r.id === selectedId) ?? null;

  // Live run rows: new runs appear, status flips stream in (US-1.32).
  useEffect(() => {
    const supabase = createClient();
    let channel: ReturnType<typeof supabase.channel> | null = null;
    let cancelled = false;

    (async () => {
      const {
        data: { session },
      } = await supabase.auth.getSession();
      if (!session || cancelled) return;
      await supabase.realtime.setAuth(session.access_token);
      channel = supabase
        .channel(`deployment-runs-${deployment.id}`, { config: { private: false } })
        .on(
          "postgres_changes",
          {
            event: "*",
            schema: "public",
            table: "deployment_runs",
            filter: `deployment_id=eq.${deployment.id}`,
          },
          (payload) => {
            if (payload.eventType === "INSERT") {
              const r = payload.new as RunRow;
              setRuns((prev) =>
                prev.some((x) => x.id === r.id) ? prev : [r, ...prev]
              );
            } else if (payload.eventType === "UPDATE") {
              const r = payload.new as RunRow;
              setRuns((prev) => prev.map((x) => (x.id === r.id ? { ...x, ...r } : x)));
            }
          }
        )
        .subscribe();
    })();

    return () => {
      cancelled = true;
      if (channel) supabase.removeChannel(channel);
    };
  }, [deployment.id]);

  // Event feed for the selected run: seed from history, then stream inserts.
  useEffect(() => {
    if (!selectedId) {
      setEvents([]);
      return;
    }
    const supabase = createClient();
    let channel: ReturnType<typeof supabase.channel> | null = null;
    let cancelled = false;

    (async () => {
      const { data } = await supabase
        .from("deployment_run_events")
        .select("id, run_id, phase, message, created_at")
        .eq("run_id", selectedId)
        .order("id", { ascending: true });
      if (cancelled) return;
      setEvents((data ?? []) as RunEvent[]);

      const {
        data: { session },
      } = await supabase.auth.getSession();
      if (!session || cancelled) return;
      await supabase.realtime.setAuth(session.access_token);
      channel = supabase
        .channel(`run-events-${selectedId}`, { config: { private: false } })
        .on(
          "postgres_changes",
          {
            event: "INSERT",
            schema: "public",
            table: "deployment_run_events",
            filter: `run_id=eq.${selectedId}`,
          },
          (payload) => {
            const e = payload.new as RunEvent;
            setEvents((prev) =>
              prev.some((x) => x.id === e.id) ? prev : [...prev, e]
            );
          }
        )
        .subscribe();
    })();

    return () => {
      cancelled = true;
      if (channel) supabase.removeChannel(channel);
    };
  }, [selectedId]);

  // Keep the live feed pinned to the newest line.
  useEffect(() => {
    const el = feedRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [events]);

  // Heartbeat (US-1.32): an active run must never look frozen — tick a
  // "seconds since last update" line even when a step is silent.
  const selectedActive =
    selected && (selected.status === "running" || selected.status === "queued");
  const [nowTick, setNowTick] = useState(Date.now());
  useEffect(() => {
    if (!selectedActive) return;
    const t = setInterval(() => setNowTick(Date.now()), 1000);
    return () => clearInterval(t);
  }, [selectedActive]);
  const lastEventAt = events.length
    ? +new Date(events[events.length - 1].created_at)
    : null;
  const sinceLast = lastEventAt ? Math.max(0, Math.round((nowTick - lastEventAt) / 1000)) : null;

  return (
    <div className="flex flex-col gap-6">
      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0">
          <div className="space-y-1.5">
            <CardTitle className="text-base">Runs</CardTitle>
            <CardDescription>
              {external ? (
                <>
                  Every run of this deployment — a merge of{" "}
                  <span className="font-mono">{deployment.branch}</span> into{" "}
                  <span className="font-mono">{deployment.target_branch}</span>,
                  and nothing after it. Logs stay viewable afterwards.
                </>
              ) : (
                "Every run of this deployment — logs stay viewable afterwards."
              )}
              {archiveTotalBytes > 0 && (
                <>
                  {" "}
                  Archived payloads:{" "}
                  {(archiveTotalBytes / 1_048_576).toFixed(1)} MB total (kept
                  indefinitely; 500 MB per-artifact limit).
                </>
              )}
            </CardDescription>
          </div>
          <div className="flex items-center gap-2">
            {memberBlocked && (
              <span className="text-xs text-muted-foreground">
                Protected — owners only
              </span>
            )}
            <AgentDispatchButton
              deploymentId={deployment.id}
              environment={deployment.environment}
              agentDispatchAllowed={!!deployment.agent_dispatch_allowed}
              isProtected={!!deployment.protected}
              externalTargetBranch={external ? deployment.target_branch : null}
            />
            {!deployment.protected && !external && (
              <RefOverrideDialog
                deploymentId={deployment.id}
                repoFullName={repoFullName}
                configuredBranch={deployment.branch}
                disabled={!!activeRun}
                onStarted={(runId) => {
                  setSelectedId(runId);
                  router.refresh();
                }}
              />
            )}
            {!external && (
            <ZipDeployDialog
              deploymentId={deployment.id}
              deploymentName={deployment.name}
              serverLabel={serverLabel}
              targetFolder={deployment.target_folder}
              stagedZip={stagedZip}
              isProtected={deployment.protected}
              disabled={!!activeRun || memberBlocked}
              onStarted={(runId) => {
                setSelectedId(runId);
                router.refresh();
              }}
            />
            )}
            <RunDeploymentDialog
              deploymentId={deployment.id}
              deploymentName={deployment.name}
              projectId={projectId}
              branch={deployment.branch}
              externalTargetBranch={external ? deployment.target_branch : null}
              serverLabel={serverLabel}
              targetFolder={deployment.target_folder}
              sourceFolder={deployment.source_folder || undefined}
              isProtected={deployment.protected}
              configChangedAreas={configChangedAreas}
              disabled={!!activeRun || memberBlocked}
              disabledReason={
                memberBlocked
                  ? "Protected — owners only"
                  : activeRun
                    ? "A run is already active for this deployment."
                    : undefined
              }
              onStarted={(runId) => {
                setSelectedId(runId);
                router.refresh();
              }}
            />
          </div>
        </CardHeader>
        <CardContent>
          {activeRun && (
            <div className="mb-3 flex items-center justify-between gap-3">
              <p className="text-xs text-muted-foreground">
                A run is active — Run is blocked until it finishes (one run at
                a time per deployment).
              </p>
              {!memberBlocked && (
              <ConfirmDialog
                trigger={
                  <Button variant="outline" size="sm">
                    <OctagonX className="size-3.5" />
                    Cancel run
                  </Button>
                }
                title="Cancel this run?"
                description="The current step is stopped promptly (a running script is terminated best-effort). Cancelling does NOT undo work already done — files already transferred and script steps already executed stay as they are."
                confirmLabel="Cancel run"
                onConfirm={async () => {
                  await apiCall(
                    `/api/v1/deployments/${deployment.id}/runs/${activeRun.id}/cancel`,
                    { method: "POST" }
                  );
                  router.refresh();
                }}
              />
              )}
            </div>
          )}
          {!external && deployment.strategy !== "releases" && (
            <p className="mb-3 text-xs text-muted-foreground">
              In-place deployment — switching to the releases strategy enables
              one-click rollback.
            </p>
          )}
          {runs.length === 0 ? (
            <EmptyState
              icon={History}
              title={external ? "Never merged" : "Never deployed"}
              description={
                external
                  ? `Run this deployment to merge ${deployment.branch} into ${deployment.target_branch}.`
                  : "Run this deployment to ship the branch to the machine."
              }
            />
          ) : (
            <ul className="grid gap-1.5">
              {runs.map((r) => {
                const rollbackable =
                  !external &&
                  deployment.strategy === "releases" &&
                  r.status === "succeeded" &&
                  r.kind === "deploy" &&
                  !activeRun &&
                  !memberBlocked;
                return (
                  <li
                    key={r.id}
                    className={cn(
                      "flex items-center justify-between gap-3 rounded-md border px-3 py-2 text-sm transition-colors",
                      selectedId === r.id && "border-ring/60 bg-muted/40"
                    )}
                  >
                    <button
                      type="button"
                      onClick={() => setSelectedId(r.id)}
                      className="flex min-w-0 flex-1 items-center gap-2 text-left"
                    >
                      <StatusBadge status={r.status as IssueStatus} />
                      {r.kind === "rollback" && (
                        <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
                          <Undo2 className="size-3" />
                          rollback
                        </span>
                      )}
                      {r.is_override && (
                        <span className="rounded bg-amber-100 px-1 text-[10px] font-semibold uppercase text-amber-700 dark:bg-amber-950 dark:text-amber-300">
                          override
                        </span>
                      )}
                      {r.redeploy_of_run_id && (
                        <span className="rounded bg-sky-100 px-1 text-[10px] font-semibold uppercase text-sky-700 dark:bg-sky-950 dark:text-sky-300">
                          redeploy
                        </span>
                      )}
                      {r.promoted_from_run_id && (
                        <span className="rounded bg-violet-100 px-1 text-[10px] font-semibold uppercase text-violet-700 dark:bg-violet-950 dark:text-violet-300">
                          promoted
                        </span>
                      )}
                      <span className="truncate text-xs text-muted-foreground">
                        {r.source === "branch" ? (
                          <>
                            <span className="font-mono">{r.branch}</span>
                            {r.commit_sha && (
                              <span className="font-mono"> @ {r.commit_sha.slice(0, 7)}</span>
                            )}
                          </>
                        ) : (
                          <span className="font-mono">
                            zip {r.zip_filename ?? ""}
                          </span>
                        )}
                      </span>
                    </button>
                    <span className="flex shrink-0 items-center gap-3 text-xs text-muted-foreground">
                      {fmtDuration(r.started_at, r.finished_at) && (
                        <span>{fmtDuration(r.started_at, r.finished_at)}</span>
                      )}
                      <span>{r.started_by_email}</span>
                      <span>{fmtWhen(r.created_at)}</span>
                      {r.status === "succeeded" &&
                        !activeRun &&
                        !memberBlocked &&
                        !external && (
                        <>
                          {r.artifact_path && (
                            <>
                              <button
                                type="button"
                                title={`Download archived payload (${(
                                  (r.artifact_bytes ?? 0) / 1_048_576
                                ).toFixed(1)} MB, sha256 ${r.artifact_sha256?.slice(0, 12)}…)`}
                                onClick={() => downloadArtifact(r)}
                                className="inline-flex items-center gap-1 hover:text-foreground"
                              >
                                <Download className="size-3.5" />
                              </button>
                              <ConfirmDialog
                                trigger={
                                  <Button variant="ghost" size="sm">
                                    <RotateCcw className="size-3.5" />
                                    Redeploy
                                  </Button>
                                }
                                title="Redeploy this run's archived payload?"
                                description={`A full new run fed from the archived bytes (${r.commit_sha?.slice(0, 7) ?? r.zip_filename ?? "payload"}, sha256 ${r.artifact_sha256?.slice(0, 12) ?? "?"}…) to ${serverLabel}:${deployment.target_folder} — no GitHub fetch, no staged zip. The deployment's CURRENT pipeline and rules apply.`}
                                confirmLabel="Redeploy"
                                requireText={
                                  deployment.protected ? deployment.name : undefined
                                }
                                onConfirm={async () => {
                                  const resp = (await apiCall(
                                    `/api/v1/deployments/${deployment.id}/runs/${r.id}/redeploy`,
                                    { method: "POST" }
                                  )) as { run_id: string };
                                  setSelectedId(resp.run_id);
                                  router.refresh();
                                }}
                              />
                            </>
                          )}
                          {r.kind === "deploy" && (
                            <PromoteDialog
                              projectId={projectId}
                              deploymentId={deployment.id}
                              runId={r.id}
                              payloadLabel={
                                r.source === "zip"
                                  ? `zip ${r.zip_filename ?? ""}`
                                  : `${r.branch} @ ${r.commit_sha?.slice(0, 7) ?? "?"}`
                              }
                              checksum={r.artifact_sha256}
                              siblings={siblings}
                            />
                          )}
                        </>
                      )}
                      {rollbackable &&
                        (r.release_path ? (
                          <ConfirmDialog
                            trigger={
                              <Button variant="ghost" size="sm">
                                <Undo2 className="size-3.5" />
                                Roll back
                              </Button>
                            }
                            title="Roll back to this release?"
                            description={`Repoints \`current\` to the release from ${fmtWhen(
                              r.created_at
                            )} (${r.commit_sha?.slice(0, 7) ?? r.source}). No files are re-transferred; the deployment script does not run again.`}
                            confirmLabel="Roll back"
                            requireText={
                              deployment.protected ? deployment.name : undefined
                            }
                            onConfirm={async () => {
                              await apiCall(
                                `/api/v1/deployments/${deployment.id}/rollback`,
                                {
                                  method: "POST",
                                  headers: { "Content-Type": "application/json" },
                                  body: JSON.stringify({ run_id: r.id }),
                                }
                              );
                              router.refresh();
                            }}
                          />
                        ) : (
                          <span title="Release pruned from the machine — redeploy from the archived payload once us-1.47 lands.">
                            release pruned
                          </span>
                        ))}
                    </span>
                  </li>
                );
              })}
            </ul>
          )}
        </CardContent>
      </Card>

      {selected && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">
              Run log
              {selected.commit_sha && (
                <span className="ml-2 font-mono text-xs text-muted-foreground">
                  {selected.branch} @ {selected.commit_sha.slice(0, 7)}
                </span>
              )}
              {external && selected.status === "succeeded" && (
                <span className="ml-2 rounded bg-emerald-100 px-1.5 text-[10px] font-semibold uppercase leading-4 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300">
                  Merged
                </span>
              )}
            </CardTitle>
            <CardDescription>
              {external ? (
                <>
                  {selected.merge_commit_sha ? (
                    <>
                      Merged into{" "}
                      <span className="font-mono">
                        {deployment.target_branch}
                      </span>{" "}
                      as{" "}
                      <span className="font-mono">
                        {selected.merge_commit_sha.slice(0, 7)}
                      </span>
                      {selected.pr_number
                        ? ` (pull request #${selected.pr_number})`
                        : ""}
                      . The merge is the whole run — what the other system does
                      next is not something this app can see.
                    </>
                  ) : (
                    <>
                      Merge stages as they happen
                      {selected.pr_number
                        ? ` — pull request #${selected.pr_number}`
                        : ""}
                      {selected.status === "running" ||
                      selected.status === "queued"
                        ? ", streaming live."
                        : "."}
                    </>
                  )}
                </>
              ) : (
                <>
                  Phases and script output as they happen{" "}
                  {selected.status === "running" || selected.status === "queued"
                    ? "— streaming live."
                    : "— finished run, timings preserved."}
                </>
              )}
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div
              ref={feedRef}
              className="max-h-96 overflow-y-auto rounded-md border bg-muted/30 p-3 font-mono text-xs"
            >
              {events.length === 0 ? (
                <p className="text-muted-foreground">
                  {selected.status === "queued" ? "Waiting to start…" : "No events."}
                </p>
              ) : (
                events.map((e, i) => {
                  const phaseChanged = i === 0 || events[i - 1].phase !== e.phase;
                  return (
                    <div key={e.id} className="flex items-start gap-2 py-px">
                      <span className="shrink-0 tabular-nums text-muted-foreground/60">
                        {fmtTime(e.created_at)}
                      </span>
                      {phaseChanged ? (
                        <span
                          className={cn(
                            "shrink-0 rounded px-1.5 text-[10px] font-semibold uppercase leading-4",
                            PHASE_STYLES[e.phase] ?? "bg-muted text-muted-foreground"
                          )}
                        >
                          {e.phase}
                        </span>
                      ) : (
                        <span className="w-[3.25rem] shrink-0" />
                      )}
                      <span className="whitespace-pre-wrap break-all">{e.message}</span>
                    </div>
                  );
                })
              )}
              {selectedActive && sinceLast !== null && sinceLast >= 3 && (
                <div className="mt-1 flex items-center gap-2 text-muted-foreground">
                  <span className="relative flex size-1.5">
                    <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-amber-500 opacity-75" />
                    <span className="relative inline-flex size-1.5 rounded-full bg-amber-500" />
                  </span>
                  working… {sinceLast}s since last update
                </div>
              )}
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
