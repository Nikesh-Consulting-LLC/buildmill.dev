import Link from "next/link";
import { redirect } from "next/navigation";
import { Rocket } from "lucide-react";

import { createClient } from "@/lib/supabase/server";
import { resolveActiveOrg } from "@/lib/active-org";
import {
  readGlobalProjectIds,
  resolveGlobalSelection,
} from "@/lib/global-project-selection";
import { CutReleaseDialog } from "./cut-release-dialog";
import { CancelReleaseButton } from "./cancel-release-button";
import { DeleteReleaseButton } from "./delete-release-button";
import { RetryReleaseButton } from "./retry-release-button";
import { EmptyState } from "@/components/empty-state";
import { GlobalProjectFilter } from "@/components/global-project-filter";
import { PageHeader } from "@/components/page-header";
import { StatusBadge, type IssueStatus } from "@/components/status-badge";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

const IN_FLIGHT = new Set([
  "queued",
  "running",
  "uat-deployed",
  "uat-signed-off",
  "promoting",
]);

// US-70.1: terminal-unsuccessful — the only statuses the delete policy allows.
const DEAD = new Set(["rejected", "failed", "cancelled"]);

const PAGE_SIZE = 25;

/** Strip the protocol so the link reads as a place, not an address. */
function urlLabel(url: string) {
  return url.replace(/^https?:\/\//, "").replace(/\/$/, "");
}

/** US-21.6: every release across the projects you can see.
 *
 * "Current in UAT" and "current in Production" are DERIVED from status, never
 * stored: a stored pointer would need updating from promotion, rollback, the
 * next release and a rejection, and would be wrong the first time one was
 * missed. US-70.1: derived from their own unpaginated query — deriving them
 * from the visible page would pin the badge to whatever older release happens
 * to be on screen. */
export default async function ReleasesPage({
  searchParams,
}: {
  searchParams: Promise<{ page?: string }>;
}) {
  const { page: pageParam } = await searchParams;
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/login");

  // Scope to the active org (US-9.7) — RLS alone permits every org the
  // caller belongs to, not just the one selected in the org switcher.
  const { orgId } = await resolveActiveOrg(supabase, user.id);
  if (!orgId) redirect("/login");

  // US-23.2: cutting happens here now, so the hub needs the projects the
  // manager can see, scoped to the active org.
  const { data: projectRows } = await supabase
    .from("projects")
    .select("id, name")
    .eq("org_id", orgId)
    .is("archived_at", null)
    .order("name", { ascending: true });
  const projects = projectRows ?? [];

  // Phase 64: the global filter narrows the tables below; cutting a release
  // (CutReleaseDialog) still offers every project, unfiltered — a view
  // filter shouldn't hide where you can create work. US-70.1: the filter is
  // applied in the query, not after fetching — filtering a fetched page
  // would make page sizes lie.
  const storedProjectIds = await readGlobalProjectIds();
  const selectedIds = resolveGlobalSelection(projects, storedProjectIds);
  const selected = [...selectedIds];
  // An empty selection can only mean "no projects"; the impossible uuid
  // keeps the query shape valid instead of matching everything.
  const projectFilter = selected.length
    ? selected
    : ["00000000-0000-0000-0000-000000000000"];

  const page = Math.max(1, parseInt(pageParam ?? "1", 10) || 1);
  const from = (page - 1) * PAGE_SIZE;

  const { data: rows, count } = await supabase
    .from("releases")
    .select(
      "id, project_id, version, status, commit_sha, included_items, created_at, released_at, uat_deployed_at, uat_deployment_run_id, prod_deployment_run_id, projects!releases_project_id_fkey(name)",
      { count: "exact" }
    )
    .eq("org_id", orgId)
    .in("project_id", projectFilter)
    .order("created_at", { ascending: false })
    .range(from, from + PAGE_SIZE - 1);

  const releases = rows ?? [];
  const total = count ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  // Newest release per project in each place, independent of the page.
  const { data: markerRows } = await supabase
    .from("releases")
    .select("id, project_id, status, created_at")
    .eq("org_id", orgId)
    .in("project_id", projectFilter)
    .in("status", ["uat-deployed", "uat-signed-off", "promoting", "released"])
    .order("created_at", { ascending: false })
    .limit(200);
  const currentUat = new Map<string, string>();
  const currentProd = new Map<string, string>();
  for (const r of markerRows ?? []) {
    if (
      !currentUat.has(r.project_id) &&
      ["uat-deployed", "uat-signed-off", "promoting"].includes(r.status)
    ) {
      currentUat.set(r.project_id, r.id);
    }
    if (!currentProd.has(r.project_id) && r.status === "released") {
      currentProd.set(r.project_id, r.id);
    }
  }

  // US-70.1: where each release actually went. The run-id columns carry no
  // foreign keys, so this is a second query, not an embed — adding FKs just
  // for an embed would put a second relationship path between releases and
  // deployment runs and invite PGRST201 across the app.
  const runIds = releases
    .flatMap((r) => [r.uat_deployment_run_id, r.prod_deployment_run_id])
    .filter(Boolean) as string[];
  const urlByRun = new Map<string, string>();
  if (runIds.length) {
    const { data: runRows } = await supabase
      .from("deployment_runs")
      .select(
        "id, deployments!deployment_runs_deployment_id_org_id_fkey(website_url)"
      )
      .in("id", runIds);
    for (const r of runRows ?? []) {
      const dep = Array.isArray(r.deployments) ? r.deployments[0] : r.deployments;
      const url = (dep as { website_url: string | null } | null)?.website_url;
      if (url) urlByRun.set(r.id as string, url);
    }
  }

  // US-70.1: the delete policy takes owner or admin; resolve the caller's
  // role so the button only renders where the delete can succeed.
  const { data: myPrincipal } = await supabase
    .from("principals")
    .select("id")
    .eq("auth_user_id", user.id)
    .maybeSingle();
  let canDelete = false;
  if (myPrincipal) {
    const { data: myMembership } = await supabase
      .from("organization_members")
      .select("role")
      .eq("org_id", orgId)
      .eq("principal_id", myPrincipal.id)
      .maybeSingle();
    canDelete =
      myMembership?.role === "owner" || myMembership?.role === "admin";
  }

  const inFlight = releases.filter((r) => IN_FLIGHT.has(r.status));
  const settled = releases.filter((r) => !IN_FLIGHT.has(r.status));

  const projectName = (r: (typeof releases)[number]) =>
    (r.projects as unknown as { name: string } | null)?.name ?? "Project";

  const envCell = (runId: string | null) => {
    const url = runId ? urlByRun.get(runId) : undefined;
    return (
      <TableCell className="hidden max-w-40 whitespace-nowrap text-xs lg:table-cell">
        {url ? (
          <a
            href={url}
            target="_blank"
            rel="noreferrer"
            className="block truncate text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
            title={url}
          >
            {urlLabel(url)}
          </a>
        ) : (
          <span className="text-muted-foreground">—</span>
        )}
      </TableCell>
    );
  };

  const rowsFor = (list: typeof releases) =>
    list.map((r) => (
      <TableRow key={r.id}>
        <TableCell className="whitespace-nowrap font-mono text-sm">
          <Link
            href={`/projects/${r.project_id}/releases/${r.id}`}
            className="underline-offset-4 hover:underline"
          >
            {r.version}
          </Link>
        </TableCell>
        <TableCell className="w-full max-w-0 text-sm">
          <span className="flex min-w-0 flex-col">
            <Link
              href={`/projects/${r.project_id}/releases`}
              className="truncate underline-offset-4 hover:underline"
            >
              {projectName(r)}
            </Link>
            {/* US-35.7: Commit, Items and Cut are columns only at `lg`. */}
            <span className="truncate text-xs text-muted-foreground lg:hidden">
              {(r.commit_sha ?? "").slice(0, 7)} ·{" "}
              {Array.isArray(r.included_items) ? r.included_items.length : 0} items ·{" "}
              {new Date(r.created_at).toLocaleDateString()}
            </span>
          </span>
        </TableCell>
        <TableCell>
          <span className="flex flex-wrap items-center gap-1.5">
            <StatusBadge status={r.status as IssueStatus} />
            {currentUat.get(r.project_id) === r.id && (
              <Badge variant="secondary" className="font-normal">
                on UAT now
              </Badge>
            )}
            {currentProd.get(r.project_id) === r.id && (
              <Badge variant="secondary" className="font-normal">
                in production now
              </Badge>
            )}
          </span>
        </TableCell>
        {envCell(r.uat_deployment_run_id)}
        {envCell(r.prod_deployment_run_id)}
        <TableCell className="hidden whitespace-nowrap font-mono text-xs text-muted-foreground lg:table-cell">
          {(r.commit_sha ?? "").slice(0, 7)}
        </TableCell>
        <TableCell className="hidden whitespace-nowrap text-xs text-muted-foreground lg:table-cell">
          {Array.isArray(r.included_items) ? r.included_items.length : 0}
        </TableCell>
        <TableCell className="hidden whitespace-nowrap text-xs text-muted-foreground lg:table-cell">
          {new Date(r.created_at).toLocaleDateString()}
        </TableCell>
        <TableCell className="whitespace-nowrap text-right">
          {r.status === "queued" && (
            <CancelReleaseButton releaseId={r.id} version={r.version} />
          )}
          {/* US-90.1: the attempt died before anything shipped — retry the
              failed leg. Rejected/cancelled rows never show this. */}
          {(r.status === "failed" || r.status === "uat-deploy-failed") && (
            <RetryReleaseButton releaseId={r.id} size="icon-sm" />
          )}
          {canDelete && DEAD.has(r.status) && (
            <DeleteReleaseButton
              releaseId={r.id}
              version={r.version}
              status={r.status}
            />
          )}
        </TableCell>
      </TableRow>
    ));

  /** US-92.3: the deployed URL for one leg of the journey, as words. */
  const envLabel = (runId: string | null) => {
    const url = runId ? urlByRun.get(runId) : undefined;
    if (!url) return "not yet";
    return urlLabel(url);
  };

  /** US-92.3: the phone form. One build, its state, how far it has got, and
   *  the buttons — full width, last, unmissable. Same data as the row. */
  const cardsFor = (rows: typeof releases) =>
    rows.map((r) => {
      const items = Array.isArray(r.included_items)
        ? r.included_items.length
        : 0;
      const actions = [
        r.status === "queued" && (
          <CancelReleaseButton
            key="cancel"
            releaseId={r.id}
            version={r.version}
          />
        ),
        (r.status === "failed" || r.status === "uat-deploy-failed") && (
          <RetryReleaseButton key="retry" releaseId={r.id} />
        ),
        canDelete && DEAD.has(r.status) && (
          <DeleteReleaseButton
            key="delete"
            releaseId={r.id}
            version={r.version}
            status={r.status}
          />
        ),
      ].filter(Boolean);

      return (
        <div key={r.id} className="grid gap-2 rounded-lg border p-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <Link
              href={`/projects/${r.project_id}/releases/${r.id}`}
              className="font-mono text-sm font-medium tabular-nums underline-offset-4 hover:underline"
            >
              {r.version}
            </Link>
            <span className="flex flex-wrap items-center gap-1.5">
              <StatusBadge status={r.status as IssueStatus} />
              {currentUat.get(r.project_id) === r.id && (
                <Badge variant="secondary" className="font-normal">
                  on UAT now
                </Badge>
              )}
              {currentProd.get(r.project_id) === r.id && (
                <Badge variant="secondary" className="font-normal">
                  in production now
                </Badge>
              )}
            </span>
          </div>

          <Link
            href={`/projects/${r.project_id}/releases`}
            className="truncate text-sm text-muted-foreground underline-offset-4 hover:underline"
          >
            {projectName(r)}
          </Link>

          {/* AC2: the two environments read as a journey, because "how far
              has this build got" is the question the page answers. */}
          <div className="flex items-center gap-2 rounded-md bg-muted/40 px-2 py-1.5 text-xs">
            <span className="shrink-0 font-medium">UAT</span>
            <span className="min-w-0 flex-1 truncate text-muted-foreground">
              {envLabel(r.uat_deployment_run_id)}
            </span>
            <span className="shrink-0 text-muted-foreground">→</span>
            <span className="shrink-0 font-medium">Prod</span>
            <span className="min-w-0 flex-1 truncate text-muted-foreground">
              {envLabel(r.prod_deployment_run_id)}
            </span>
          </div>

          {/* AC4: the diagnostics, in one muted line that does not compete
              with the version. */}
          <p className="truncate font-mono text-xs text-muted-foreground">
            {(r.commit_sha ?? "").slice(0, 7)} · {items} item
            {items === 1 ? "" : "s"} · cut{" "}
            {new Date(r.created_at).toLocaleDateString()}
          </p>

          {/* AC3: the buttons are the reason for the visit — full width and
              a real tap size, whatever the shared components default to. */}
          {actions.length > 0 && (
            <div className="flex flex-wrap gap-2 border-t pt-2 [&_button]:h-10 [&_button]:flex-1">
              {actions}
            </div>
          )}
        </div>
      );
    });

  const header = (
    <TableHeader>
      <TableRow>
        <TableHead>Version</TableHead>
        <TableHead className="w-full max-w-0">Project</TableHead>
        <TableHead>Status</TableHead>
        {/* US-70.1: where it went, columns only at `lg`. */}
        <TableHead className="hidden lg:table-cell">UAT</TableHead>
        <TableHead className="hidden lg:table-cell">Production</TableHead>
        {/* US-35.7: the diagnostic pair, columns only at `lg`. */}
        <TableHead className="hidden lg:table-cell">Commit</TableHead>
        <TableHead className="hidden lg:table-cell">Items</TableHead>
        <TableHead className="hidden lg:table-cell">Cut</TableHead>
        <TableHead className="w-24 text-right">Actions</TableHead>
      </TableRow>
    </TableHeader>
  );

  const pager = totalPages > 1 && (
    <div className="flex items-center justify-between text-sm text-muted-foreground">
      <span>
        {total} releases · page {page} of {totalPages}
      </span>
      <span className="flex gap-3">
        {page > 1 ? (
          <Link
            href={`/releases?page=${page - 1}`}
            className="underline-offset-4 hover:text-foreground hover:underline"
          >
            ← Newer
          </Link>
        ) : (
          <span className="opacity-50">← Newer</span>
        )}
        {page < totalPages ? (
          <Link
            href={`/releases?page=${page + 1}`}
            className="underline-offset-4 hover:text-foreground hover:underline"
          >
            Older →
          </Link>
        ) : (
          <span className="opacity-50">Older →</span>
        )}
      </span>
    </div>
  );

  return (
    <div className="flex w-full flex-col gap-6">
      <PageHeader
        title="Releases"
        description="Every build cut across your projects, and where each one is."
        actions={projects.length > 0 && <CutReleaseDialog projects={projects} />}
        filter={
          projects.length > 0 && (
            <GlobalProjectFilter
              projects={projects}
              initialSelected={[...selectedIds]}
            />
          )
        }
      />

      {total === 0 ? (
        <EmptyState
          icon={Rocket}
          title="No releases yet"
          description="Cut one from a project's Releases tab to pin the current head of its default branch and ship it to UAT."
        />
      ) : (
        <>
          {inFlight.length > 0 && (
            <Card>
              <CardHeader>
                <CardTitle className="text-base">In flight</CardTitle>
                <CardDescription>
                  Cut and not yet finished — being built, tested, or promoted.
                </CardDescription>
              </CardHeader>
              {/* US-92.3: nine columns in 314px crushed every cell and
                  detached the headers from their values. Cards below `md`. */}
              <CardContent className="grid gap-2 md:hidden">
                {cardsFor(inFlight)}
              </CardContent>
              <CardContent className="hidden min-w-0 overflow-x-auto md:block">
                <Table>
                  {header}
                  <TableBody>{rowsFor(inFlight)}</TableBody>
                </Table>
              </CardContent>
            </Card>
          )}

          {settled.length > 0 && (
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Finished</CardTitle>
                <CardDescription>
                  Released, rolled back, rejected or failed.
                </CardDescription>
              </CardHeader>
              <CardContent className="grid gap-2 md:hidden">
                {cardsFor(settled)}
              </CardContent>
              <CardContent className="hidden min-w-0 overflow-x-auto md:block">
                <Table>
                  {header}
                  <TableBody>{rowsFor(settled)}</TableBody>
                </Table>
              </CardContent>
            </Card>
          )}

          {pager}
        </>
      )}
    </div>
  );
}
