import Link from "next/link";
import { redirect } from "next/navigation";
import {
  AlertTriangle,
  Bot,
  ExternalLink,
  FolderGit2,
  GitBranch,
  Rocket,
  User,
} from "lucide-react";
import { createClient } from "@/lib/supabase/server";
import { resolveActiveOrg } from "@/lib/active-org";
import { githubRepoUrl } from "@/lib/factory-git";
import { cn } from "@/lib/utils";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ProjectDialog } from "./project-dialog";
import { ProjectActions } from "./project-actions";
import { ProjectSpend } from "./project-spend";

type Assignee = { id: string; name: string; kind: string };
/** US-91.9: "deployed Aug 13, 6:56 PM" — the same shape the rest of the app
 *  uses for a recent moment. */
function formatWhen(iso: string) {
  return new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

type DeploymentCard = {
  id: string;
  name: string;
  environment: string | null;
  website_url: string | null;
  lastStatus: string | null;
  /** US-91.9: what is SERVING right now — the release whose build this
   *  deployment's live run carries. Null when the live run belongs to no
   *  release (a branch deploy, an uploaded zip, a manual override), in which
   *  case `liveCommit` names it instead. Never borrowed from the newest
   *  release: what is live is a fact about the deployment. */
  liveVersion: string | null;
  liveReleaseId: string | null;
  /** Short sha (or zip filename) of an off-release build. */
  liveCommit: string | null;
  /** When the live run finished — "deployed <when>" on the card. */
  liveAt: string | null;
};

export default async function ProjectsPage({
  searchParams,
}: {
  searchParams: Promise<{ view?: string }>;
}) {
  const { view } = await searchParams;
  const showArchived = view === "archived";
  const supabase = await createClient();

  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/login");

  // US-9.7: scope to the active org.
  const { orgId } = await resolveActiveOrg(supabase, user.id);
  if (!orgId) redirect("/login");

  const { data: projects } = await supabase
    .from("projects")
    .select(
      "id, name, slug, description, repo_full_name, default_branch, updated_at, archived_at, budget_enabled, budget_usd"
    )
    .eq("org_id", orgId)
    .order("created_at", { ascending: false });

  const active = (projects ?? []).filter((p) => !p.archived_at);
  const archived = (projects ?? []).filter((p) => p.archived_at);
  const list = showArchived ? archived : active;
  const projectIds = list.map((p) => p.id);

  // A project row can exist with no GitHub connection behind it (the org
  // link is separate from the project), and every path that acts on a
  // project's repo — PR review, merges, releases — needs one. Surfacing
  // this here, not just on /settings/github, is what stops a manager from
  // creating projects for a while before discovering nothing can actually
  // ship.
  const { count: githubConnectionCount } = await supabase
    .from("github_connections")
    .select("id", { count: "exact", head: true })
    .eq("org_id", orgId);
  const githubLinked = (githubConnectionCount ?? 0) > 0;

  // US-37.4: spend for every project on the page, in ONE query. The list
  // already does per-card enrichment for people and deployments; adding an N+1
  // to it for money is how a projects page gets slow.
  const { data: spendRows } = await supabase.rpc("org_project_spend", {
    p_org: orgId,
  });
  const spendByProject = new Map<string, { spent: number; unmeasured: number }>(
    (
      (spendRows ?? []) as {
        project_id: string;
        spent_usd: number | null;
        unmeasured_calls: number | null;
      }[]
    ).map((r) => [
      r.project_id,
      {
        spent: Number(r.spent_usd ?? 0),
        unmeasured: Number(r.unmeasured_calls ?? 0),
      },
    ])
  );

  // US-10.15: per-card people & agents assigned to the project, and its
  // deployments with last-run status. (Factory remote + Connect live on Team;
  // work-item creation/monitoring lives on the Work Items hub.)
  const assignedByProject = new Map<string, Assignee[]>();
  const deploymentsByProject = new Map<string, DeploymentCard[]>();

  if (projectIds.length) {
    // US-31.10: the card's two halves have two sources, because the domain
    // does. HUMANS are assigned to work items (issues.assignee_id). AGENTS
    // are assigned to the PROJECT — worker_capabilities rows, the same ones
    // the pool listing, the claim gate and the clone gate enforce
    // (us-31.3). Reading only issues.assignee_id is why a project with four
    // capable agents read "No one assigned yet".
    const { data: assignedRows } = await supabase
      .from("issues")
      .select("project_id, assignee_id")
      .in("project_id", projectIds)
      .not("assignee_id", "is", null);
    const { data: grantRows } = await supabase
      .from("worker_capabilities")
      .select("project_id, worker_id")
      .in("project_id", projectIds);

    // Grants name a worker; the display name lives on its principal.
    const workerIds = [
      ...new Set((grantRows ?? []).map((g) => g.worker_id as string)),
    ];
    const principalByWorker = new Map<string, string>();
    if (workerIds.length) {
      const { data: workers } = await supabase
        .from("workers")
        .select("id, principal_id")
        .in("id", workerIds);
      for (const w of workers ?? []) {
        if (w.principal_id) principalByWorker.set(w.id, w.principal_id);
      }
    }

    const pIds = [
      ...new Set([
        ...(assignedRows ?? []).map((a) => a.assignee_id as string),
        ...principalByWorker.values(),
      ]),
    ];
    const principalById = new Map<
      string,
      { display_name: string | null; email: string | null; kind: string }
    >();
    if (pIds.length) {
      const { data: prins } = await supabase
        .from("principals")
        .select("id, display_name, email, kind")
        .in("id", pIds);
      for (const p of prins ?? []) principalById.set(p.id, p);
    }
    const seen = new Set<string>();
    const push = (projectId: string, principalId: string) => {
      const key = `${projectId}:${principalId}`;
      if (seen.has(key)) return;
      const p = principalById.get(principalId);
      if (!p) return;
      seen.add(key);
      const arr = assignedByProject.get(projectId) ?? [];
      arr.push({
        id: principalId,
        name: p.display_name || p.email || "Someone",
        kind: p.kind,
      });
      assignedByProject.set(projectId, arr);
    };
    // Agents first: they are the project's standing capacity, while an
    // item assignee is incidental to one piece of work.
    for (const g of grantRows ?? []) {
      const principalId = principalByWorker.get(g.worker_id as string);
      if (principalId) push(g.project_id as string, principalId);
    }
    for (const a of assignedRows ?? []) {
      push(a.project_id as string, a.assignee_id as string);
    }

    // Deployments + last-run status per deployment.
    const { data: deps } = await supabase
      .from("deployments")
      // US-91.9: current_run_id is the run this deployment is serving — the
      // same row the deployment detail page reads, so the two cannot disagree.
      .select("id, project_id, name, environment, website_url, current_run_id")
      .in("project_id", projectIds)
      .order("created_at", { ascending: true });
    const depIds = (deps ?? []).map((d) => d.id);
    const lastStatus = new Map<string, string>();
    if (depIds.length) {
      const { data: runs } = await supabase
        .from("deployment_runs")
        .select("deployment_id, status, created_at")
        .in("deployment_id", depIds)
        .order("created_at", { ascending: false });
      for (const r of runs ?? []) {
        if (!lastStatus.has(r.deployment_id))
          lastStatus.set(r.deployment_id, r.status);
      }
    }

    // US-91.9: the live build per deployment — two batched reads for the whole
    // page (the runs being served, then the releases they belong to), never
    // one per project and never one per deployment.
    const liveByDeployment = new Map<
      string,
      {
        version: string | null;
        releaseId: string | null;
        commit: string | null;
        at: string | null;
      }
    >();
    const currentRunIds = (deps ?? [])
      .map((d) => d.current_run_id)
      .filter((id): id is string => !!id);
    if (currentRunIds.length) {
      const { data: liveRuns } = await supabase
        .from("deployment_runs")
        .select(
          "id, deployment_id, release_id, commit_sha, zip_filename, finished_at, created_at"
        )
        .in("id", currentRunIds);
      const releaseIds = [
        ...new Set(
          (liveRuns ?? [])
            .map((r) => r.release_id)
            .filter((id): id is string => !!id)
        ),
      ];
      const versionById = new Map<string, string>();
      if (releaseIds.length) {
        const { data: rels } = await supabase
          .from("releases")
          .select("id, version")
          .in("id", releaseIds);
        for (const r of rels ?? []) versionById.set(r.id, r.version);
      }
      for (const r of liveRuns ?? []) {
        const version = r.release_id
          ? (versionById.get(r.release_id) ?? null)
          : null;
        liveByDeployment.set(r.deployment_id, {
          at: (r.finished_at as string | null) ?? (r.created_at as string | null),
          version,
          releaseId: version ? r.release_id : null,
          // AC4: an off-release deploy says so. Borrowing the last release's
          // version here would be the worst possible lie on this card.
          commit: version
            ? null
            : (r.commit_sha?.slice(0, 7) ?? r.zip_filename ?? null),
        });
      }
    }
    for (const d of deps ?? []) {
      const arr = deploymentsByProject.get(d.project_id) ?? [];
      const live = liveByDeployment.get(d.id);
      arr.push({
        id: d.id,
        name: d.name,
        environment: d.environment,
        website_url: d.website_url,
        lastStatus: lastStatus.get(d.id) ?? null,
        liveVersion: live?.version ?? null,
        liveReleaseId: live?.releaseId ?? null,
        liveCommit: live?.commit ?? null,
        liveAt: live?.at ?? null,
      });
      deploymentsByProject.set(d.project_id, arr);
    }
  }

  return (
    <div className="flex w-full flex-col gap-6">
      <PageHeader
        title="Projects"
        description="Each project links the factory to a GitHub repository."
        actions={<ProjectDialog orgId={orgId} />}
      />

      {!githubLinked && (projects?.length ?? 0) > 0 && (
        <Link
          href="/settings/github"
          className="flex items-center gap-3 rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900 transition-colors hover:bg-amber-100 dark:border-amber-900 dark:bg-amber-950/70 dark:text-amber-200 dark:hover:bg-amber-900/50"
        >
          <AlertTriangle className="size-4 shrink-0" />
          <span className="min-w-0 flex-1">
            <span className="font-semibold">GitHub not linked:</span> this
            workspace has no connected GitHub account, so nothing here can be
            read, cloned, or merged.
          </span>
          <span className="shrink-0 font-medium underline underline-offset-4">
            Connect GitHub
          </span>
        </Link>
      )}

      <Tabs value={showArchived ? "archived" : "active"}>
        <TabsList>
          <TabsTrigger value="active" render={<Link href="/projects" />}>
            Active ({active.length})
          </TabsTrigger>
          <TabsTrigger value="archived" render={<Link href="/projects?view=archived" />}>
            Archived ({archived.length})
          </TabsTrigger>
        </TabsList>

        <TabsContent value={showArchived ? "archived" : "active"}>
          {!list.length ? (
            <EmptyState
              icon={FolderGit2}
              title={showArchived ? "No archived projects" : "No projects yet"}
              description={
                showArchived
                  ? "Projects you archive will show up here."
                  : "Create your first project to point the factory at a repository."
              }
            />
          ) : (
            <div className="grid gap-4 sm:grid-cols-2">
              {list.map((p) => {
                const assigned = assignedByProject.get(p.id) ?? [];
                const deployments = deploymentsByProject.get(p.id) ?? [];
                return (
                  <Card key={p.id} className="h-full transition-colors hover:border-ring/60">
                    <CardHeader className="flex flex-row items-start justify-between space-y-0">
                      <Link href={`/projects/${p.id}`} className="min-w-0 flex-1">
                        <CardTitle className="flex items-center gap-2 text-base">
                          <FolderGit2 className="size-4 text-muted-foreground" />
                          <span className="truncate">{p.name}</span>
                        </CardTitle>
                        {p.description && (
                          <CardDescription className="line-clamp-2">
                            {p.description}
                          </CardDescription>
                        )}
                      </Link>
                      <ProjectSpend project={p} spend={spendByProject.get(p.id)} />
                    </CardHeader>
                    {/* US-92.6: `order` puts deployments above the roster
                        below `md` without a second copy of either block. */}
                    <CardContent className="flex flex-col gap-2 text-sm text-muted-foreground">
                      <div className="flex items-center justify-between gap-2">
                        <span className="flex min-w-0 items-center gap-2">
                          <a
                            href={githubRepoUrl(p.repo_full_name)}
                            target="_blank"
                            rel="noreferrer"
                            title="Open on GitHub"
                            className="flex min-w-0 items-center gap-1 font-mono text-xs hover:text-foreground hover:underline"
                          >
                            <span className="truncate">{p.repo_full_name}</span>
                            <ExternalLink className="size-3 shrink-0" />
                          </a>
                          <Badge variant="secondary" className="gap-1 font-normal">
                            <GitBranch className="size-3" />
                            {p.default_branch}
                          </Badge>
                        </span>
                        <ProjectActions
                          projectId={p.id}
                          name={p.name}
                          archivedAt={p.archived_at}
                        />
                      </div>

                      {/* US-10.15: people & agents assigned to this project.
                          US-92.6: below `md` the chips fold behind their own
                          count — three rows of them is a screenful spent on
                          what a manager rarely acts on from a phone. The
                          chips themselves are written once and placed twice,
                          so the two cannot drift. */}
                      <div className="order-2 border-t pt-2 md:order-none">
                        {(() => {
                          const chips =
                            assigned.length === 0 ? (
                              /* US-31.10: name which half is empty — "no one
                                 assigned" was wrong in two different ways. */
                              <p className="text-xs text-muted-foreground">
                                No agents granted, and no one assigned to its
                                work items.
                              </p>
                            ) : (
                              <div className="flex flex-wrap gap-1">
                                {assigned.map((a) => (
                                  <span
                                    key={a.id}
                                    className="inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs text-foreground"
                                  >
                                    {a.kind === "agent" ? (
                                      <Bot className="size-3 text-muted-foreground" />
                                    ) : (
                                      <User className="size-3 text-muted-foreground" />
                                    )}
                                    {a.name}
                                  </span>
                                ))}
                              </div>
                            );
                          return (
                            <>
                              <details className="md:hidden">
                                <summary className="cursor-pointer list-none text-[11px] font-medium uppercase tracking-wide text-muted-foreground/70">
                                  {assigned.length === 0
                                    ? "No people or agents"
                                    : `${assigned.length} people & agents`}
                                </summary>
                                <div className="mt-1.5">{chips}</div>
                              </details>
                              <div className="hidden md:block">
                                <div className="mb-1 text-[11px] font-medium uppercase tracking-wide text-muted-foreground/70">
                                  People &amp; agents
                                </div>
                                {chips}
                              </div>
                            </>
                          );
                        })()}
                      </div>

                      {/* US-10.15: compact deployment cards. */}
                      <div className="order-1 border-t pt-2 md:order-none">
                        <div className="mb-1 flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-muted-foreground/70">
                          <Rocket className="size-3" /> Deployments
                        </div>
                        {deployments.length === 0 ? (
                          <p className="text-xs text-muted-foreground">
                            No deployments configured.
                          </p>
                        ) : (
                          <div className="grid gap-1.5 sm:grid-cols-2">
                            {deployments.map((d) => (
                              <Link
                                key={d.id}
                                href={`/projects/${p.id}/deployments/${d.id}`}
                                className="flex flex-col gap-0.5 rounded-md border p-2 text-xs transition-colors hover:border-ring/60"
                              >
                                <span className="flex items-center gap-1.5">
                                  <span className="font-medium text-foreground">{d.name}</span>
                                  {d.environment && (
                                    <Badge variant="secondary" className="font-normal">
                                      {d.environment}
                                    </Badge>
                                  )}
                                </span>
                                {d.website_url && (
                                  <span className="truncate text-muted-foreground">
                                    {d.website_url}
                                  </span>
                                )}
                                {/* US-91.9: which build is live, said in
                                    words. A version is date-shaped, so
                                    unlabelled it reads as a date; a bare sha
                                    reads as noise. The label names what it
                                    is, the timestamp says when it went out,
                                    and the release is a real link. */}
                                {(d.liveVersion || d.liveCommit) && (
                                  <span className="flex min-w-0 flex-wrap items-baseline gap-x-1 text-muted-foreground">
                                    <span className="shrink-0">Live:</span>
                                    {d.liveVersion ? (
                                      <Link
                                        href={`/projects/${p.id}/releases/${d.liveReleaseId}`}
                                        className="font-mono tabular-nums text-foreground underline decoration-dotted underline-offset-4 hover:decoration-solid"
                                        title={`Release ${d.liveVersion} — open it`}
                                      >
                                        {d.liveVersion}
                                      </Link>
                                    ) : (
                                      <span
                                        className="font-mono"
                                        title="Deployed outside a release — no version names this build"
                                      >
                                        commit {d.liveCommit}
                                      </span>
                                    )}
                                    {d.liveAt && (
                                      <span className="truncate">
                                        · deployed {formatWhen(d.liveAt)}
                                      </span>
                                    )}
                                  </span>
                                )}
                                <span className="flex items-center gap-1 text-muted-foreground">
                                  <span
                                    className={cn(
                                      "size-1.5 rounded-full",
                                      d.lastStatus === "succeeded"
                                        ? "bg-emerald-500"
                                        : d.lastStatus === "failed"
                                          ? "bg-red-500"
                                          : "bg-muted-foreground/40"
                                    )}
                                  />
                                  {d.lastStatus ?? "Never deployed"}
                                </span>
                              </Link>
                            ))}
                          </div>
                        )}
                      </div>
                    </CardContent>
                  </Card>
                );
              })}
            </div>
          )}
        </TabsContent>
      </Tabs>
    </div>
  );
}
