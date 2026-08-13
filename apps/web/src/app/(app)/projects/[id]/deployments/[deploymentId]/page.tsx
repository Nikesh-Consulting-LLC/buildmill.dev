import Link from "next/link";
import { notFound, redirect } from "next/navigation";
import { ArrowLeft, GitBranch, GitMerge, Server, ShieldAlert } from "lucide-react";
import { createClient } from "@/lib/supabase/server";
import { Badge } from "@/components/ui/badge";
import { RunPanel, type RunRow } from "./run-panel";
import { EnvVarsPanel } from "./env-vars-panel";
import { PreflightDialog } from "./preflight-dialog";
import { NotificationsCard } from "./notifications-card";
import { DriftCard, type CurrentRun } from "./drift-card";
import { AgentDeployFlag } from "../agent-deploy-flag";
import { HealthCheckButton } from "./health-check-button";
import { HistoryCard, type ConfigEvent } from "./history-card";
import { IssueReportingCard } from "./issue-reporting-card";

export default async function DeploymentDetailPage({
  params,
}: {
  params: Promise<{ id: string; deploymentId: string }>;
}) {
  const { id, deploymentId } = await params;
  const supabase = await createClient();

  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/login");

  const { data: deployment } = await supabase
    .from("deployments")
    .select(
      "id, org_id, project_id, kind, server_id, name, branch, target_branch, target_folder, script, run_timeout_minutes, strategy, keep_releases, source_folder, staged_zip_filename, staged_zip_bytes, staged_zip_uploaded_at, current_run_id, health_check_url, protected, environment, agent_dispatch_allowed, issue_reporting_enabled, issue_report_key_last4, is_self_monitoring"
    )
    .eq("id", deploymentId)
    .maybeSingle();

  if (!deployment || deployment.project_id !== id) notFound();

  // US-50.1: an external deployment has no machine, and US-50.3 removes every
  // action that would reach one — the page must show only what it can do.
  const isExternal = deployment.kind === "external";

  const { data: project } = await supabase
    .from("projects")
    .select("id, name, repo_full_name")
    .eq("id", id)
    .maybeSingle();
  if (!project) notFound();

  const { data: server } = deployment.server_id
    ? await supabase
        .from("servers")
        .select("id, name, host, username")
        .eq("id", deployment.server_id)
        .maybeSingle()
    : { data: null };

  const { data: envVars } = isExternal
    ? { data: [] as { name: string }[] }
    : await supabase
        .from("deployment_env_vars")
        .select("name")
        .eq("deployment_id", deployment.id)
        .order("name", { ascending: true });

  const { data: runs } = await supabase
    .from("deployment_runs")
    .select(
      "id, status, source, kind, branch, commit_sha, zip_filename, is_override, release_path, merge_commit_sha, pr_number, artifact_path, artifact_bytes, artifact_sha256, redeploy_of_run_id, promoted_from_run_id, started_by_email, started_at, finished_at, created_at"
    )
    .eq("deployment_id", deployment.id)
    .order("created_at", { ascending: false })
    .limit(30);

  // US-1.34: "currently deployed" reads from the durable pointer.
  let currentRun = null;
  if (deployment.current_run_id) {
    const { data } = await supabase
      .from("deployment_runs")
      .select(
        "source, branch, commit_sha, commit_message, zip_filename, finished_at, started_by_email, is_override"
      )
      .eq("id", deployment.current_run_id)
      .maybeSingle();
    currentRun = data;
  }

  const { data: notificationRow } = await supabase
    .from("deployment_notifications")
    .select("events")
    .eq("deployment_id", deployment.id)
    .maybeSingle();

  const { count: endpointCount } = await supabase
    .from("notification_endpoints")
    .select("id", { count: "exact", head: true })
    .eq("org_id", deployment.org_id);

  const { data: ownRole } = await supabase
    .from("organization_members")
    .select("role")
    .eq("org_id", deployment.org_id)
    .eq("user_id", user.id)
    .maybeSingle();
  const isOwner = ownRole?.role === "owner";

  // US-1.43: promotion targets — same-project siblings.
  const { data: allServers } = await supabase
    .from("servers")
    .select("id, name, host")
    .eq("org_id", deployment.org_id);
  const { data: siblingRows } = await supabase
    .from("deployments")
    .select("id, name, kind, target_folder, protected, server_id")
    .eq("project_id", deployment.project_id)
    .neq("id", deployment.id)
    // US-50.3: a promotion ships a payload, and an external deployment has
    // nowhere to put one — it is not a promotion target.
    .eq("kind", "factory")
    .order("name", { ascending: true });
  const siblings = (siblingRows ?? []).map((s) => {
    const srv = (allServers ?? []).find((x) => x.id === s.server_id);
    return {
      id: s.id,
      name: s.name,
      target_folder: s.target_folder ?? "",
      protected: s.protected,
      serverLabel: srv ? `${srv.name} (${srv.host})` : "unknown machine",
    };
  });

  // US-1.47: total archive storage used by this deployment.
  const { data: artifactRows } = await supabase
    .from("deployment_runs")
    .select("artifact_bytes")
    .eq("deployment_id", deployment.id)
    .not("artifact_bytes", "is", null)
    .limit(1000);
  const archiveTotalBytes = (artifactRows ?? []).reduce(
    (sum, r) => sum + (r.artifact_bytes ?? 0),
    0
  );

  // US-1.49: audit trail + "config changed since last successful run".
  const { data: configEvents } = await supabase
    .from("deployment_events")
    .select("id, actor, event, areas, detail, created_at")
    .eq("deployment_id", deployment.id)
    .order("id", { ascending: false })
    .limit(50);

  const { data: lastSuccess } = await supabase
    .from("deployment_runs")
    .select("created_at")
    .eq("deployment_id", deployment.id)
    .eq("status", "succeeded")
    .order("created_at", { ascending: false })
    .limit(1)
    .maybeSingle();

  const changedAreas = new Set<string>();
  if (lastSuccess) {
    for (const e of configEvents ?? []) {
      if (e.created_at > lastSuccess.created_at) {
        for (const a of (e.areas as string[]) ?? []) changedAreas.add(a);
      }
    }
  }

  const serverLabel = server ? `${server.name} (${server.host})` : "unknown machine";

  return (
    <div className="flex w-full flex-col gap-6">
      <div className="min-w-0">
        <Link
          href={`/projects/${project.id}`}
          className="mb-1 inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="size-3" />
          {project.name}
        </Link>
        <div className="flex items-center justify-between gap-3">
          <div className="flex min-w-0 items-center gap-2">
            <h1 className="truncate text-2xl font-semibold tracking-tight">
              {deployment.name}
            </h1>
            {deployment.protected && (
              <Badge className="gap-1 border-red-200 bg-red-100 font-normal text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
                <ShieldAlert className="size-3" />
                Protected
              </Badge>
            )}
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {deployment.environment === "production" &&
              !deployment.protected && (
                <AgentDeployFlag
                  deploymentId={deployment.id}
                  enabled={!!deployment.agent_dispatch_allowed}
                />
              )}
            {!isExternal && deployment.health_check_url && (
              <HealthCheckButton deploymentId={deployment.id} />
            )}
            {!isExternal && <PreflightDialog deploymentId={deployment.id} />}
          </div>
        </div>
        <div className="mt-1.5 flex flex-wrap items-center gap-2 text-sm">
          <Badge variant="secondary" className="gap-1 font-normal">
            <GitBranch className="size-3" />
            {isExternal
              ? `${deployment.branch} → ${deployment.target_branch}`
              : deployment.branch}
          </Badge>
          {isExternal ? (
            <>
              <Badge variant="outline" className="gap-1 font-normal">
                <GitMerge className="size-3" />
                External
              </Badge>
              <span className="text-xs text-muted-foreground">
                Deployed by merging into{" "}
                <span className="font-mono">{deployment.target_branch}</span> —
                whatever pipeline watches it takes over from there. Nothing is
                copied to a machine.
              </span>
            </>
          ) : (
            <>
              <Badge variant="secondary" className="gap-1 font-normal">
                <Server className="size-3" />
                {serverLabel}
              </Badge>
              <span className="font-mono text-xs text-muted-foreground">
                {deployment.target_folder}
              </span>
              {deployment.source_folder && (
                <span className="text-xs text-muted-foreground">
                  source folder{" "}
                  <span className="font-mono">{deployment.source_folder}</span>
                </span>
              )}
            </>
          )}
        </div>
      </div>

      <DriftCard
        deploymentId={deployment.id}
        currentRun={currentRun as CurrentRun}
        externalTargetBranch={isExternal ? deployment.target_branch : null}
        backTo={`from=${encodeURIComponent(`/projects/${id}/deployments/${deploymentId}`)}&fromLabel=${encodeURIComponent(deployment.name)}`}
      />

      <RunPanel
        deployment={{
          id: deployment.id,
          name: deployment.name,
          kind: deployment.kind,
          branch: deployment.branch,
          target_branch: deployment.target_branch,
          target_folder: deployment.target_folder ?? "",
          strategy: deployment.strategy,
          source_folder: deployment.source_folder,
          protected: deployment.protected,
          environment: deployment.environment,
          agent_dispatch_allowed: deployment.agent_dispatch_allowed,
        }}
        isOwner={isOwner}
        configChangedAreas={[...changedAreas]}
        repoFullName={project.repo_full_name}
        projectId={project.id}
        siblings={siblings}
        archiveTotalBytes={archiveTotalBytes}
        serverLabel={serverLabel}
        stagedZip={
          deployment.staged_zip_filename
            ? {
                filename: deployment.staged_zip_filename,
                uploadedAt: deployment.staged_zip_uploaded_at ?? "",
                bytes: deployment.staged_zip_bytes ?? 0,
              }
            : null
        }
        initialRuns={(runs ?? []) as RunRow[]}
      />

      {/* US-50.3: env var values are written onto a target machine. An
          external deployment has nowhere to write them, and offering the
          field would promise the other system reads them. */}
      {!isExternal && (
        <EnvVarsPanel
          deploymentId={deployment.id}
          names={(envVars ?? []).map((v) => v.name)}
        />
      )}

      <NotificationsCard
        orgId={deployment.org_id}
        deploymentId={deployment.id}
        initialEvents={(notificationRow?.events as string[] | undefined) ?? null}
        hasEndpoints={(endpointCount ?? 0) > 0}
      />

      {/* US-16.3: reporting is configured where the website URL and health
          checks already are — it is this deployment's own wiring. */}
      <IssueReportingCard
        orgId={deployment.org_id}
        deploymentId={deployment.id}
        initialEnabled={deployment.issue_reporting_enabled ?? false}
        initialLast4={deployment.issue_report_key_last4 ?? null}
        initialSelfMonitoring={deployment.is_self_monitoring ?? false}
      />

      <HistoryCard events={(configEvents ?? []) as unknown as ConfigEvent[]} />
    </div>
  );
}
