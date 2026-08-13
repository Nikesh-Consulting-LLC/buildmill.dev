"use client";

import Link from "next/link";
import { useRouter } from "@/lib/router-with-progress";
import {
  ExternalLink,
  GitBranch,
  GitMerge,
  Rocket,
  Server,
  ShieldAlert,
  Trash2,
} from "lucide-react";
import { apiCall } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { EmptyState } from "@/components/empty-state";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { StatusBadge, type IssueStatus } from "@/components/status-badge";
import { DeploymentDialog } from "./deployment-dialog";
import { ReleaseTargetsCard } from "./release-targets-card";
import { DuplicateDialog } from "./duplicate-dialog";
import { RunDeploymentDialog } from "./deployments/[deploymentId]/run-deployment-dialog";
import { BuildConfigCard } from "./build-config-card";

export type LastRun = { status: string; created_at: string };

export type DeployedNow = {
  source: string;
  commit_sha: string | null;
  commit_message: string | null;
  zip_filename: string | null;
  finished_at: string | null;
  started_by_email: string | null;
};

export type DeploymentRow = {
  id: string;
  name: string;
  /** US-50.1: `factory` (this app ships it over SSH) or `external` (a merge
   * into the branch somebody else's pipeline watches). Set at creation. */
  kind: string;
  server_id: string | null;
  environment: string | null;
  website_kind: string | null;
  website_url: string | null;
  branch: string;
  target_branch: string;
  target_folder: string | null;
  script: string;
  run_timeout_minutes: number;
  strategy: string;
  keep_releases: number;
  source_folder: string;
  exclude_patterns: string;
  current_run_id: string | null;
  health_check_url: string;
  health_check_expected_status: number;
  health_check_window_seconds: number;
  health_check_initial_delay_seconds: number;
  protected: boolean;
  updated_at: string;
};

export type ServerOption = {
  id: string;
  name: string;
  host: string;
  username: string;
  auth_method: string;
  key_fingerprint: string | null;
};

export function DeploymentsTab({
  orgId,
  projectId,
  repoFullName,
  uatBranch,
  productionBranch,
  deployments,
  servers,
  lastRuns,
  deployedNow,
  isOwner,
  llmConfigured,
  releaseUatDeploymentId,
  releaseProdDeploymentId,
}: {
  orgId: string;
  projectId: string;
  repoFullName: string;
  uatBranch: string | null;
  productionBranch: string | null;
  deployments: DeploymentRow[];
  servers: ServerOption[];
  lastRuns: Record<string, LastRun>;
  deployedNow: Record<string, DeployedNow>;
  isOwner: boolean;
  llmConfigured: boolean;
  /** US-21.1: which deployment a release ships to. */
  releaseUatDeploymentId: string | null;
  releaseProdDeploymentId: string | null;
}) {
  const router = useRouter();

  async function handleDelete(id: string) {
    // Through api (not the SDK): deleting a deployment must also clean up
    // its bucket folder — env var values (US-1.37) and later artifacts.
    await apiCall(`/api/v1/deployments/${id}`, { method: "DELETE" });
    router.refresh();
  }

  return (
    <div className="flex flex-col gap-6">
    <ReleaseTargetsCard
      projectId={projectId}
      uatDeploymentId={releaseUatDeploymentId}
      prodDeploymentId={releaseProdDeploymentId}
      deployments={deployments.map((d) => ({
        id: d.id,
        name: d.name,
        environment: d.environment,
      }))}
    />
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0">
        <div className="space-y-1.5">
          <CardTitle className="text-base">Deployments</CardTitle>
          <CardDescription>
            Reusable definitions of where and how this project ships — run on
            demand, never automatically.
          </CardDescription>
        </div>
        <DeploymentDialog
          orgId={orgId}
          projectId={projectId}
          repoFullName={repoFullName}
          uatBranch={uatBranch}
          productionBranch={productionBranch}
          servers={servers}
          isOwner={isOwner}
          llmConfigured={llmConfigured}
        />
      </CardHeader>
      <CardContent>
        {deployments.length === 0 ? (
          <EmptyState
            icon={Rocket}
            title="No deployments yet"
            description="Define where this project ships — a machine, a branch, a target folder, and the script that starts it."
          />
        ) : (
          <ul className="grid gap-1.5">
            {deployments.map((d) => {
              const external = d.kind === "external";
              const server = servers.find((s) => s.id === d.server_id);
              const serverLabel = server
                ? `${server.name} (${server.host})`
                : "Unknown machine";
              // US-50.1: mixed projects are the point, so a row says how it
              // ships — the machine for a factory deployment, source → target
              // for an external one.
              const shipsVia = external
                ? `merges into ${d.target_branch}`
                : serverLabel;
              const last = lastRuns[d.id];
              const memberBlocked = d.protected && !isOwner;
              return (
                <li
                  key={d.id}
                  className="flex items-center justify-between gap-3 rounded-md border px-3 py-2 text-sm"
                >
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <Link
                        href={`/projects/${projectId}/deployments/${d.id}`}
                        className="truncate font-medium underline-offset-4 hover:underline"
                      >
                        {d.name}
                      </Link>
                      <Badge variant="secondary" className="gap-1 font-normal">
                        <GitBranch className="size-3" />
                        {external ? `${d.branch} → ${d.target_branch}` : d.branch}
                      </Badge>
                      {external && (
                        <Badge variant="outline" className="font-normal">
                          External
                        </Badge>
                      )}
                      {d.environment && (
                        <Badge variant="outline" className="font-normal capitalize">
                          {d.environment}
                        </Badge>
                      )}
                      {d.website_url && (
                        <a
                          href={d.website_url}
                          target="_blank"
                          rel="noopener"
                          className="inline-flex max-w-[16rem] items-center gap-1 truncate text-xs text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
                          title={d.website_url}
                        >
                          <ExternalLink className="size-3 shrink-0" />
                          <span className="truncate">
                            {d.website_url.replace(/^https?:\/\//, "")}
                          </span>
                        </a>
                      )}
                      {d.protected && (
                        <Badge className="gap-1 border-red-200 bg-red-100 font-normal text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
                          <ShieldAlert className="size-3" />
                          Protected
                        </Badge>
                      )}
                      {last ? (
                        <span className="flex items-center gap-1.5">
                          <StatusBadge status={last.status as IssueStatus} />
                          <span className="text-xs text-muted-foreground">
                            {new Date(last.created_at).toLocaleString(undefined, {
                              month: "short",
                              day: "numeric",
                              hour: "2-digit",
                              minute: "2-digit",
                            })}
                          </span>
                        </span>
                      ) : (
                        <span className="text-xs text-muted-foreground">
                          Never deployed
                        </span>
                      )}
                    </div>
                    <p className="mt-0.5 flex items-center gap-1.5 text-xs text-muted-foreground">
                      {external ? (
                        <GitMerge className="size-3 shrink-0" />
                      ) : (
                        <Server className="size-3 shrink-0" />
                      )}
                      <span className="truncate">
                        {shipsVia}
                        {!external && (
                          <>
                            {" · "}
                            <span className="font-mono">{d.target_folder}</span>
                          </>
                        )}
                        {" · "}
                        {deployedNow[d.id] ? (
                          <span className="font-mono">
                            deployed{" "}
                            {deployedNow[d.id].source === "zip"
                              ? `zip ${deployedNow[d.id].zip_filename ?? ""}`
                              : deployedNow[d.id].commit_sha?.slice(0, 7)}
                          </span>
                        ) : (
                          "never deployed"
                        )}
                      </span>
                    </p>
                    {deployedNow[d.id] &&
                      (() => {
                        // US-2.13: the whole story — message (first line),
                        // when, and by whom — not a bare SHA.
                        const dn = deployedNow[d.id];
                        const msg =
                          dn.source === "zip"
                            ? dn.zip_filename
                            : dn.commit_message?.split("\n")[0];
                        return (
                          <p className="mt-0.5 truncate pl-[18px] text-xs text-muted-foreground">
                            {msg ? <span>{msg} · </span> : null}
                            {dn.finished_at &&
                              new Date(dn.finished_at).toLocaleString(undefined, {
                                month: "short",
                                day: "numeric",
                                hour: "2-digit",
                                minute: "2-digit",
                              })}
                            {dn.started_by_email ? ` · by ${dn.started_by_email}` : ""}
                          </p>
                        );
                      })()}
                  </div>
                  <div className="flex shrink-0 items-center gap-1">
                    <RunDeploymentDialog
                      deploymentId={d.id}
                      deploymentName={d.name}
                      projectId={projectId}
                      branch={d.branch}
                      externalTargetBranch={external ? d.target_branch : null}
                      serverLabel={serverLabel}
                      targetFolder={d.target_folder ?? ""}
                      sourceFolder={d.source_folder || undefined}
                      isProtected={d.protected}
                      disabled={
                        memberBlocked ||
                        last?.status === "queued" ||
                        last?.status === "running"
                      }
                      disabledReason={
                        memberBlocked
                          ? "Protected — owners only"
                          : "A run is already active for this deployment."
                      }
                      onStarted={() =>
                        router.push(`/projects/${projectId}/deployments/${d.id}`)
                      }
                    />
                    {memberBlocked ? (
                      <span
                        className="px-2 text-xs text-muted-foreground"
                        title="Protected — owners only"
                      >
                        Protected — owners only
                      </span>
                    ) : (
                      <>
                        <DeploymentDialog
                          orgId={orgId}
                          projectId={projectId}
                          repoFullName={repoFullName}
                          uatBranch={uatBranch}
                          productionBranch={productionBranch}
                          servers={servers}
                          deployment={d}
                          isOwner={isOwner}
                          llmConfigured={llmConfigured}
                        />
                        <DuplicateDialog deploymentId={d.id} sourceName={d.name} />
                        <ConfirmDialog
                          trigger={
                            <Button variant="ghost" size="sm">
                              <Trash2 className="size-3.5" />
                              Delete
                            </Button>
                          }
                          title={`Delete deployment "${d.name}"?`}
                          description="This removes the deployment definition. Nothing on the machine is touched."
                          confirmLabel="Delete deployment"
                          requireText={d.protected ? d.name : undefined}
                          onConfirm={() => handleDelete(d.id)}
                        />
                      </>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </CardContent>
    </Card>
    <BuildConfigCard projectId={projectId} />
    </div>
  );
}
