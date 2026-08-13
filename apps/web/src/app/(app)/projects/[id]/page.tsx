import Link from "next/link";
import { notFound, redirect } from "next/navigation";
import {
  ArrowLeft,
  Cable,
  ExternalLink,
  History,
  Layers,
  ListTodo,
  Plus,
  ScrollText,
} from "lucide-react";
import { createClient } from "@/lib/supabase/server";
import { factoryRemoteUrl, githubRepoUrl } from "@/lib/factory-git";
import { CopyButton } from "@/components/copy-button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { EmptyState } from "@/components/empty-state";
import { StageDots } from "@/components/stage-tracker";
import { StatusBadge, type IssueStatus } from "@/components/status-badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { BudgetCard } from "./budget-card";
import { loadOrgCapabilities } from "@/lib/permissions";
import {
  DocumentsPanel,
  type DocumentLinkMeta,
} from "@/components/documents-panel";
import type { DocumentRow } from "@/lib/documents";
import { fetchActorNames } from "@/lib/approvals";
import { ProjectDialog } from "../project-dialog";
import { ProjectActions } from "../project-actions";
import { StartNewEpicButton } from "./start-new-epic-button";
import { ProjectSummaryCard } from "./project-summary-card";
import { RefreshGuidelinesDialog } from "./refresh-guidelines-dialog";
import { ProjectSetupReadinessCard } from "./project-setup-readiness-card";
import { epicLabel, workItemDisplayId } from "@/lib/work-items";
import { GuidelinesTab } from "./guidelines-tab";
import type { GuidelineSectionRow } from "./guideline-section-card";
import { LearningsTab } from "./learnings-tab";
import { WorkerInstructionsTab } from "./worker-instructions-tab";
import { GithubTab } from "./github-tab";
import {
  DeploymentsTab,
  type DeploymentRow,
  type ServerOption,
} from "./deployments-tab";
import { SuitesTab, type SuiteRow } from "./suites-tab";
import { EnvironmentTab } from "./environment-tab";

export default async function ProjectDetailPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams?: Promise<{ tab?: string; section?: string }>;
}) {
  const { id } = await params;
  // US-7.7: readiness deep-links land on the tab that resolves each check.
  // US-20.1: `section` selects one of Worker Instructions' sections.
  const search = await searchParams;
  const initialTab = search?.tab ?? "overview";
  const initialSection = search?.section;
  const supabase = await createClient();

  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/login");

  const { data: project } = await supabase
    .from("projects")
    .select(
      "id, org_id, name, slug, description, summary, repo_full_name, default_branch, created_at, updated_at, archived_at, env_runtime, env_setup_commands, env_notes, uat_branch, production_branch, dev_branch_strategy, guidelines_ready_at, guidelines_ready_by, worker_instructions_ready_at, worker_instructions_ready_by, docs_tree_enabled, build_mode, sequential_only, follow_build_order, route_feature_as_one, auto_approve_prd, auto_approve_plan, auto_approve_code, release_uat_deployment_id, release_prod_deployment_id, instructions_synced_at, instructions_synced_sha, budget_enabled, budget_usd, budget_started_at, org_template_id, presubmit_test_command"
    )
    .eq("id", id)
    .maybeSingle();

  if (!project) notFound();

  // Phase 67 (us-67.1): which project template this project was silently
  // seeded from — provenance only, read-only, visible to every role.
  const { data: projectTemplate } = project.org_template_id
    ? await supabase
        .from("org_project_templates")
        .select("name")
        .eq("id", project.org_template_id)
        .maybeSingle()
    : { data: null };

  const { data: ownRole } = await supabase
    .from("organization_members")
    .select("role")
    .eq("org_id", project.org_id)
    .eq("user_id", user.id)
    .maybeSingle();
  const isOwner = ownRole?.role === "owner";

  // US-37.1: what this project has spent since its budget started, and whether
  // the viewer may change the budget. `project_spend_usd` runs under the
  // caller's rights, so llm_usage's org-member RLS is what scopes it.
  const caps = await loadOrgCapabilities(supabase, project.org_id, user.id);
  const { data: spentRaw } = await supabase.rpc("project_spend_usd", {
    p_project: project.id,
  });
  const { count: unmeasuredCount } = await supabase
    .from("llm_usage")
    .select("id", { count: "exact", head: true })
    .eq("project_id", project.id)
    .is("cost_usd", null);

  // US-3.9: GitHub link + factory remote in the header.
  const { data: org } = await supabase
    .from("organizations")
    .select("shortname")
    .eq("id", project.org_id)
    .maybeSingle();
  const remoteUrl = org?.shortname
    ? factoryRemoteUrl(org.shortname, project.slug)
    : null;

  const { data: issues } = await supabase
    .from("issues")
    .select(
      "id, title, type, status, updated_at, epic_id, item_no, sub_no, abandoned_at, epics(number)"
    )
    .eq("project_id", project.id)
    .order("created_at", { ascending: false });

  const { data: epics } = await supabase
    .from("epics")
    .select("id, title, number, status, active")
    .eq("project_id", project.id)
    .order("number", { ascending: true });
  const activeEpic = (epics ?? []).find((e) => e.active) ?? null;
  // US-7.10: the Overview Work Items section is scoped to the active epic;
  // items still open in it block starting a new epic.
  const EPIC_OPEN_STATUSES = new Set([
    "draft", "prd-review", "ready", "planning", "plan-review", "planned",
    "queued", "running", "needs-fixes", "in-review", "failed",
  ]);
  const activeEpicIssues = (issues ?? []).filter(
    (i) => i.epic_id === activeEpic?.id
  );
  const epicBlockers = activeEpicIssues
    .filter((i) => !i.abandoned_at && EPIC_OPEN_STATUSES.has(i.status))
    .map((i) => ({
      id: i.id,
      title: i.title,
      displayId: workItemDisplayId({
        type: i.type,
        epicNumber: activeEpic?.number ?? null,
        itemNo: i.item_no,
        subNo: i.sub_no,
      }),
    }));

  const { data: guidelines } = await supabase
    .from("project_guidelines")
    .select("id, section_key, title, content, sort_order, updated_at")
    .eq("project_id", project.id)
    .order("sort_order", { ascending: true });

  const { data: learnings } = await supabase
    .from("project_learnings")
    .select("content, last_updated_by, updated_at")
    .eq("project_id", project.id)
    .maybeSingle();

  // US-5.6: which worker contributed what — the curated document keeps
  // no attribution, the submissions log does.
  // us-5.31: pending submissions are the manager's gate — fetched with
  // their status so the tab can split "waiting on you" from history.
  const { data: learningSubmissions } = await supabase
    .from("learning_submissions")
    .select("id, text, created_at, status, workers(name)")
    .eq("project_id", project.id)
    .order("created_at", { ascending: false })
    .limit(50);

  // US-5.14: per-kind worker instruction templates (seeded by migration 052).
  const { data: workerInstructions } = await supabase
    .from("worker_instructions")
    .select("id, run_kind, content, updated_by, updated_at")
    .eq("project_id", project.id);
  const instructionActorNames = await fetchActorNames(
    supabase,
    (workerInstructions ?? []).map((w) => w.updated_by)
  );
  // US-7.4 / US-7.5: who marked guidelines / worker instructions ready.
  const readyActorNames = await fetchActorNames(supabase, [
    project.guidelines_ready_by,
    project.worker_instructions_ready_by,
  ]);

  const { data: deployments } = await supabase
    .from("deployments")
    .select(
      "id, name, kind, server_id, environment, website_kind, website_url, branch, target_branch, target_folder, script, run_timeout_minutes, strategy, keep_releases, source_folder, exclude_patterns, current_run_id, health_check_url, health_check_expected_status, health_check_window_seconds, health_check_initial_delay_seconds, protected, updated_at"
    )
    .eq("project_id", project.id)
    .order("name", { ascending: true });

  const { data: servers } = await supabase
    .from("servers")
    .select("id, name, host, username, auth_method, key_fingerprint")
    .eq("org_id", project.org_id)
    .order("name", { ascending: true });

  // US-81.1: the project's declared automated test suites.
  const { data: testSuites } = await supabase
    .from("test_suites")
    .select(
      "id, name, layer, run_command, results_path, server_id, run_on_uat, run_on_prod, blocks_signoff, timeout_minutes, status"
    )
    .eq("project_id", project.id)
    .order("name", { ascending: true });

  // US-82.3: the project's declared modules.
  const { data: projectModules } = await supabase
    .from("project_modules")
    .select("id, name, path_globs")
    .eq("project_id", project.id)
    .order("name", { ascending: true });

  // US-1.51: Generate-with-AI is disabled until an org LLM is configured.
  const { data: llmProviders } = await supabase
    .from("llm_providers")
    .select("id")
    .eq("org_id", project.org_id)
    .limit(1);
  const llmConfigured = !!llmProviders?.length;

  // Newest-first run rows reduce to each deployment's last run (US-1.32).
  const lastRuns: Record<string, { status: string; created_at: string }> = {};
  const deploymentIds = (deployments ?? []).map((d) => d.id);
  if (deploymentIds.length > 0) {
    const { data: runRows } = await supabase
      .from("deployment_runs")
      .select("deployment_id, status, created_at")
      .in("deployment_id", deploymentIds)
      .order("created_at", { ascending: false })
      .limit(100);
    for (const r of runRows ?? []) {
      if (!lastRuns[r.deployment_id]) {
        lastRuns[r.deployment_id] = { status: r.status, created_at: r.created_at };
      }
    }
  }

  // US-2.21/2.22: every project document with its link target resolved.
  const { data: docRows } = await supabase
    .from("documents")
    .select("*, issues(id, title), test_cases(id, title)")
    .eq("project_id", project.id)
    .order("created_at", { ascending: false });
  const documents = (docRows ?? []) as unknown as (DocumentRow & {
    issues: { id: string; title: string } | null;
    test_cases: { id: string; title: string } | null;
  })[];
  const docLinkMeta: Record<string, DocumentLinkMeta> = {};
  const backToProject = `from=${encodeURIComponent(`/projects/${project.id}`)}&fromLabel=${encodeURIComponent(project.name)}`;
  for (const d of documents) {
    if (d.attached_to === "work-item" && d.issues) {
      docLinkMeta[d.id] = {
        label: d.issues.title,
        href: `/issues/${d.issues.id}?${backToProject}`,
      };
    } else if (d.attached_to === "prd" && d.issues) {
      docLinkMeta[d.id] = {
        label: `PRD · ${d.issues.title}`,
        href: `/issues/${d.issues.id}?${backToProject}`,
      };
    } else if (d.attached_to === "test-case" && d.test_cases) {
      docLinkMeta[d.id] = { label: `Test · ${d.test_cases.title}`, href: "/tests" };
    } else {
      docLinkMeta[d.id] = { label: "Project", href: null };
    }
  }
  const docActorNames = await fetchActorNames(
    supabase,
    documents.map((d) => d.created_by)
  );

  // US-1.34/US-2.13: currently deployed payload per deployment (durable
  // pointer) — with the commit message, when, and by whom.
  const deployedNow: Record<
    string,
    {
      source: string;
      commit_sha: string | null;
      commit_message: string | null;
      zip_filename: string | null;
      finished_at: string | null;
      started_by_email: string | null;
    }
  > = {};
  const currentRunIds = (deployments ?? [])
    .map((d) => d.current_run_id)
    .filter((id): id is string => !!id);
  if (currentRunIds.length > 0) {
    const { data: currentRows } = await supabase
      .from("deployment_runs")
      .select(
        "id, deployment_id, source, commit_sha, commit_message, zip_filename, finished_at, started_by_email"
      )
      .in("id", currentRunIds);
    for (const r of currentRows ?? []) {
      deployedNow[r.deployment_id] = {
        source: r.source,
        commit_sha: r.commit_sha,
        commit_message: r.commit_message,
        zip_filename: r.zip_filename,
        finished_at: r.finished_at,
        started_by_email: r.started_by_email,
      };
    }
  }

  return (
    <div className="flex w-full flex-col gap-6">
      <div className="flex items-center justify-between gap-4">
        <div className="min-w-0">
          <Link
            href="/projects"
            className="mb-1 inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
          >
            <ArrowLeft className="size-3" />
            Projects
          </Link>
          <div className="flex items-center gap-2">
            <h1 className="truncate text-2xl font-semibold tracking-tight">
              {project.name}
            </h1>
            <span className="font-mono text-xs text-muted-foreground">
              {project.slug}
            </span>
            {project.archived_at && <Badge variant="secondary">Archived</Badge>}
          </div>
          {project.description && (
            <p className="text-sm text-muted-foreground">
              {project.description}
            </p>
          )}
          <div className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
            <a
              href={githubRepoUrl(project.repo_full_name)}
              target="_blank"
              rel="noreferrer"
              title="Open on GitHub"
              className="flex items-center gap-1 font-mono hover:text-foreground hover:underline"
            >
              {project.repo_full_name}
              <ExternalLink className="size-3" />
            </a>
            {remoteUrl && (
              <span className="flex min-w-0 items-center gap-1">
                <span
                  className="truncate font-mono"
                  title="Factory git remote — agents clone and push here"
                >
                  {remoteUrl}
                </span>
                <CopyButton text={remoteUrl} title="Copy factory git remote URL" />
                <Button
                  variant="ghost"
                  size="sm"
                  title="Connect a tool to this project"
                  render={<Link href={`/projects/${project.id}/connect`} />}
                >
                  <Cable className="size-3.5" />
                  Connect
                </Button>
              </span>
            )}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            render={<Link href={`/projects/${project.id}/epics`} />}
          >
            <Layers className="size-4" />
            Epics
          </Button>
          <Button
            variant="outline"
            size="sm"
            render={<Link href={`/projects/${project.id}/audit`} />}
          >
            <ScrollText className="size-4" />
            Audit
          </Button>
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label="Overview history"
            title="Who changed the project overview, and what it said before"
            render={
              <Link href={`/projects/${project.id}/audit?surface=project`} />
            }
          >
            <History className="size-4" />
          </Button>
          <ProjectDialog orgId={project.org_id} project={project} />
          <ProjectActions
            projectId={project.id}
            name={project.name}
            archivedAt={project.archived_at}
            redirectOnDelete
          />
        </div>
      </div>

      <Tabs defaultValue={initialTab}>
        {/* US-7.6: tab order follows the setup flow. */}
        <TabsList>
          <TabsTrigger value="overview">Overview</TabsTrigger>
          <TabsTrigger value="guidelines">Guidelines</TabsTrigger>
          <TabsTrigger value="worker-instructions">
            Agent Instructions
          </TabsTrigger>
          <TabsTrigger value="deployments">Deployments</TabsTrigger>
          <TabsTrigger value="suites">Suites</TabsTrigger>
          <TabsTrigger value="environment">Environment</TabsTrigger>
          <TabsTrigger value="documents">Documents</TabsTrigger>
          <TabsTrigger value="learnings">Learnings</TabsTrigger>
          <TabsTrigger value="github">GitHub</TabsTrigger>
        </TabsList>

        <TabsContent value="overview" className="flex flex-col gap-6">
          {/* US-43.2: a whole-document decision, so it sits with the
              project's other whole-project actions rather than on the
              Guidelines tab, where sections are edited one at a time. */}
          {caps.can("manage_project") ? (
            <div className="flex justify-end">
              <RefreshGuidelinesDialog
                projectId={project.id}
                hasRepo={!!project.repo_full_name}
              />
            </div>
          ) : null}
          {projectTemplate?.name && (
            <p className="text-xs text-muted-foreground">
              Created from template: {projectTemplate.name}
            </p>
          )}
          <ProjectSummaryCard
            orgId={project.org_id}
            projectId={project.id}
            summary={project.summary}
          />
          {/* US-20.1: Task processing moved to Worker Instructions, where it
              sits first — it governs everything the instructions describe. */}
          <BudgetCard
            projectId={project.id}
            budgetEnabled={!!project.budget_enabled}
            budgetUsd={project.budget_usd}
            budgetStartedAt={project.budget_started_at}
            spent={Number(spentRaw ?? 0)}
            unmeasured={unmeasuredCount ?? 0}
            canManage={caps.can("manage_project")}
          />
          <ProjectSetupReadinessCard
            checks={(() => {
              const base = `/projects/${project.id}`;
              const setup = (guidelines ?? []).find(
                (g) => g.section_key === "run-commands"
              );
              const runCmds = !!(setup?.content ?? "").trim();
              const envOk =
                !!(project.env_runtime ?? "").trim() ||
                ((project.env_setup_commands as string[] | null)?.length ?? 0) > 0;
              const deploys = (deployments ?? []) as DeploymentRow[];
              const hasUat = deploys.some(
                (d) => d.environment === "uat" && !!d.website_url
              );
              const hasProd = deploys.some(
                (d) => d.environment === "production" && !!d.website_url
              );
              return [
                {
                  label: "Project Summary written",
                  detail: "The foundation the rest is generated from.",
                  done: !!(project.summary ?? "").trim(),
                  href: `${base}?tab=overview`,
                },
                {
                  label: "Repository & release branches",
                  detail: "UAT and Production release branches picked.",
                  done: !!project.uat_branch && !!project.production_branch,
                  href: `${base}?tab=github`,
                },
                {
                  label: "Guidelines marked ready",
                  detail: "Reviewed and marked good to go.",
                  done: !!project.guidelines_ready_at,
                  href: `${base}?tab=guidelines`,
                },
                {
                  label: "Agent Instructions marked ready",
                  detail: "Reviewed and marked good to go.",
                  done: !!project.worker_instructions_ready_at,
                  href: `${base}?tab=worker-instructions`,
                },
                {
                  label: "Build & test config defined",
                  detail:
                    "Runtime + setup and the build/test/lint verify commands.",
                  done: envOk && runCmds,
                  href: `${base}?tab=guidelines`,
                },
                {
                  label: "UAT + Production deployments with a Website",
                  detail: "Each environment reachable at a Website.",
                  done: hasUat && hasProd,
                  href: `${base}?tab=deployments`,
                },
              ];
            })()}
          />
        </TabsContent>

        <TabsContent value="documents">
          <DocumentsPanel
            orgId={project.org_id}
            projectId={project.id}
            target={{ attachedTo: "project" }}
            initialDocs={documents}
            actorNames={docActorNames}
            linkMeta={docLinkMeta}
            description="Every document in this project's folder — created here, by the factory, or by agents. Attached ones link to their work item, PRD, or test case."
            emptyTitle="No documents yet"
            emptyDescription="Upload project-level files here, or attach documents on a work item."
          />
        </TabsContent>

        <TabsContent value="guidelines">
          <GuidelinesTab
            canRefresh={caps.can("manage_project")}
            hasRepo={!!project.repo_full_name}
            orgId={project.org_id}
            projectId={project.id}
            projectName={project.name}
            sections={(guidelines ?? []) as GuidelineSectionRow[]}
            environment={{
              env_runtime: project.env_runtime ?? "",
              env_setup_commands:
                (project.env_setup_commands as string[] | null) ?? [],
              env_notes: project.env_notes ?? "",
            }}
            guidelinesReadyAt={project.guidelines_ready_at}
            guidelinesReadyByName={
              project.guidelines_ready_by
                ? readyActorNames[project.guidelines_ready_by] ?? null
                : null
            }
            instructionsSyncedAt={project.instructions_synced_at}
            instructionsSyncedSha={project.instructions_synced_sha}
            repoFullName={project.repo_full_name}
            defaultBranch={project.default_branch}
          />
        </TabsContent>

        <TabsContent value="learnings">
          <LearningsTab
            orgId={project.org_id}
            projectId={project.id}
            learnings={learnings ?? null}
            submissions={(learningSubmissions ?? []).map((s) => {
              const w = s.workers as unknown as
                | { name: string }
                | { name: string }[]
                | null;
              return {
                id: s.id,
                text: s.text,
                createdAt: s.created_at,
                status: s.status,
                worker: (Array.isArray(w) ? w[0]?.name : w?.name) ?? "worker",
              };
            })}
          />
        </TabsContent>

        <TabsContent value="worker-instructions">
          <WorkerInstructionsTab
            rows={workerInstructions ?? []}
            actorNames={instructionActorNames}
            orgId={project.org_id}
            projectId={project.id}
            readyAt={project.worker_instructions_ready_at}
            readyByName={
              project.worker_instructions_ready_by
                ? readyActorNames[project.worker_instructions_ready_by] ?? null
                : null
            }
            initialSection={initialSection}
            // US-86.1: the two routing switches; the fallbacks mirror the
            // column defaults (both NOT NULL, so they never fire).
            followBuildOrder={project.follow_build_order ?? true}
            routeFeatureAsOne={project.route_feature_as_one ?? true}
            autoApprovePrd={project.auto_approve_prd ?? false}
            autoApprovePlan={project.auto_approve_plan ?? false}
            autoApproveCode={project.auto_approve_code ?? false}
          />
        </TabsContent>

        <TabsContent value="github">
          <GithubTab
            projectId={project.id}
            repoFullName={project.repo_full_name}
            defaultBranch={project.default_branch}
            uatBranch={project.uat_branch}
            productionBranch={project.production_branch}
            devBranchStrategy={project.dev_branch_strategy}
            docsTreeEnabled={project.docs_tree_enabled}
          />
        </TabsContent>

        <TabsContent value="deployments">
          <DeploymentsTab
            orgId={project.org_id}
            projectId={project.id}
            repoFullName={project.repo_full_name}
            uatBranch={project.uat_branch}
            productionBranch={project.production_branch}
            deployments={(deployments ?? []) as DeploymentRow[]}
            servers={(servers ?? []) as ServerOption[]}
            lastRuns={lastRuns}
            deployedNow={deployedNow}
            isOwner={isOwner}
            llmConfigured={llmConfigured}
            releaseUatDeploymentId={project.release_uat_deployment_id}
            releaseProdDeploymentId={project.release_prod_deployment_id}
          />
        </TabsContent>

        <TabsContent value="environment">
          <EnvironmentTab projectId={project.id} orgId={project.org_id} />
        </TabsContent>

        <TabsContent value="suites">
          <SuitesTab
            orgId={project.org_id}
            projectId={project.id}
            suites={(testSuites ?? []) as SuiteRow[]}
            servers={(servers ?? []).map((s) => ({ id: s.id, name: s.name }))}
            presubmitTestCommand={project.presubmit_test_command}
            modules={(projectModules ?? []).map((m) => ({
              ...m,
              path_globs: (m.path_globs as string[]) ?? [],
            }))}
          />
        </TabsContent>
      </Tabs>
    </div>
  );
}
