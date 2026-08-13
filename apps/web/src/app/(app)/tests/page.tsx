import Link from "next/link";
import { redirect } from "next/navigation";
import { CheckCircle2, CircleDashed, FolderGit2, XCircle } from "lucide-react";
import { createClient } from "@/lib/supabase/server";
import { resolveActiveOrg } from "@/lib/active-org";
import { cn } from "@/lib/utils";
import {
  readGlobalProjectIds,
  resolveGlobalSelection,
} from "@/lib/global-project-selection";
import { EmptyState } from "@/components/empty-state";
import { GlobalProjectFilter } from "@/components/global-project-filter";
import { PageHeader } from "@/components/page-header";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { RunTestsDialog } from "./run-tests-dialog";
import { TestCaseDialog } from "./test-case-dialog";
import { TestLibrary, type TestCaseRow } from "./test-library";

function formatWhen(iso: string) {
  return new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default async function TestsPage({
  searchParams,
}: {
  searchParams: Promise<{ project?: string }>;
}) {
  const { project: projectParam } = await searchParams;
  const supabase = await createClient();

  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/login");

  const { orgId } = await resolveActiveOrg(supabase, user.id);
  if (!orgId) redirect("/login");

  const { data: projects } = await supabase
    .from("projects")
    .select("id, name, org_id")
    .eq("org_id", orgId)
    .order("created_at", { ascending: true });

  if (!projects?.length) {
    return (
      <div className="flex w-full flex-col gap-6">
        <PageHeader
          title="Tests"
          description="A living library of test cases per project."
        />
        <EmptyState
          icon={FolderGit2}
          title="No projects yet"
          description="Create a project first — test cases live inside projects."
        />
      </div>
    );
  }

  // Phase 64: the global filter narrows which projects are even choosable
  // here — Testing is inherently one project at a time, so it still needs a
  // "current" pick, but only from among what the global filter shows.
  const storedProjectIds = await readGlobalProjectIds();
  const selectedProjectIds = resolveGlobalSelection(projects, storedProjectIds);
  const visibleProjects = projects.filter((p) => selectedProjectIds.has(p.id));

  if (!visibleProjects.length) {
    return (
      <div className="flex w-full flex-col gap-6">
        <PageHeader
          title="Tests"
          description="A living library of test cases — yours and the agents&apos; — run against the environment you choose."
          filter={<GlobalProjectFilter projects={projects} initialSelected={[]} />}
        />
        <EmptyState
          icon={FolderGit2}
          title="No projects selected"
          description="Pick one or more projects in the filter above to see test cases."
        />
      </div>
    );
  }

  const selected =
    visibleProjects.find((p) => p.id === projectParam) ?? visibleProjects[0];

  const [
    { data: testCases },
    { data: issues },
    { data: runs },
    { data: suites },
    { data: modules },
  ] =
    await Promise.all([
      supabase
        .from("test_cases")
        .select(
          "id, title, steps, expected_result, source, status, test_types, environments, issue_id, execution, suite_id, spec_ref, always_on_uat, module_id"
        )
        .eq("project_id", selected.id)
        .is("release_id", null)
        .order("created_at", { ascending: false }),
      supabase
        .from("issues")
        .select("id, title")
        .eq("project_id", selected.id)
        .order("created_at", { ascending: false }),
      supabase
        .from("test_runs")
        .select(
          "id, environment, label, status, created_at, test_run_results(result)"
        )
        .eq("project_id", selected.id)
        .order("created_at", { ascending: false })
        .limit(8),
      supabase
        .from("test_suites")
        .select("id, name")
        .eq("project_id", selected.id)
        .order("name", { ascending: true }),
      supabase
        .from("project_modules")
        .select("id, name")
        .eq("project_id", selected.id)
        .order("name", { ascending: true }),
    ]);

  const cases = (testCases ?? []).map((c) => ({
    ...c,
    test_types: (c.test_types as string[]) ?? [],
    environments: (c.environments as string[]) ?? [],
  })) as TestCaseRow[];

  return (
    <div className="flex w-full flex-col gap-6">
      <PageHeader
        title="Tests"
        description="A living library of test cases — yours and the agents&apos; — run against the environment you choose."
        actions={
          <>
            <TestCaseDialog
              orgId={selected.org_id}
              projectId={selected.id}
              issues={issues ?? []}
              modules={modules ?? []}
            />
            <RunTestsDialog
              orgId={selected.org_id}
              projectId={selected.id}
              userId={user.id}
              testCases={cases}
            />
          </>
        }
        filter={
          <GlobalProjectFilter
            projects={projects}
            initialSelected={[...selectedProjectIds]}
          />
        }
      />

      <TestLibrary
        orgId={selected.org_id}
        projectId={selected.id}
        testCases={cases}
        issues={issues ?? []}
        suites={suites ?? []}
        modules={modules ?? []}
      />

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Run history</CardTitle>
          <CardDescription>
            Every run records which environment it was executed against.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {!runs?.length ? (
            <p className="text-sm text-muted-foreground">No runs yet.</p>
          ) : (
            <ul className="grid gap-2">
              {runs.map((r) => {
                const results = (r.test_run_results ?? []) as {
                  result: string;
                }[];
                const passed = results.filter((x) => x.result === "pass").length;
                const failed = results.filter((x) => x.result === "fail").length;
                const pending = results.filter(
                  (x) => x.result === "pending"
                ).length;
                return (
                  <li key={r.id}>
                    <Link
                      href={`/tests/runs/${r.id}`}
                      className="flex flex-wrap items-center gap-3 rounded-md border px-3 py-2 text-sm transition-colors hover:border-ring/60"
                    >
                      <span className="font-medium">{r.label || r.environment}</span>
                      <span className="text-xs text-muted-foreground">
                        {formatWhen(r.created_at)}
                      </span>
                      <span className="ml-auto flex items-center gap-3 text-xs">
                        <span className="inline-flex items-center gap-1 text-emerald-600 dark:text-emerald-400">
                          <CheckCircle2 className="size-3.5" />
                          {passed}
                        </span>
                        <span className="inline-flex items-center gap-1 text-destructive">
                          <XCircle className="size-3.5" />
                          {failed}
                        </span>
                        {pending > 0 && (
                          <span className="inline-flex items-center gap-1 text-muted-foreground">
                            <CircleDashed className="size-3.5" />
                            {pending} pending
                          </span>
                        )}
                        <span
                          className={cn(
                            "rounded-full border px-2 py-0.5",
                            r.status === "completed"
                              ? "text-muted-foreground"
                              : "border-amber-500/40 text-amber-600 dark:text-amber-400"
                          )}
                        >
                          {r.status}
                        </span>
                      </span>
                    </Link>
                  </li>
                );
              })}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
