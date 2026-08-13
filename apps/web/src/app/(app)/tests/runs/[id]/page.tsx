import Link from "next/link";
import { notFound, redirect } from "next/navigation";
import { ArrowLeft } from "lucide-react";
import { createClient } from "@/lib/supabase/server";
import { Badge } from "@/components/ui/badge";
import { RunChecklist, type ChecklistItem } from "./run-checklist";

function formatWhen(iso: string) {
  return new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default async function TestRunPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const supabase = await createClient();

  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/login");

  const { data: run } = await supabase
    .from("test_runs")
    .select("id, environment, label, status, created_at, project_id, projects(name)")
    .eq("id", id)
    .maybeSingle();
  if (!run) notFound();

  const { data: results } = await supabase
    .from("test_run_results")
    .select(
      "id, result, note, test_cases(title, steps, expected_result)"
    )
    .eq("test_run_id", id);

  const items: ChecklistItem[] = (results ?? []).map((r) => {
    const tc = r.test_cases as unknown as {
      title: string;
      steps: string;
      expected_result: string;
    } | null;
    return {
      id: r.id,
      result: r.result,
      note: r.note,
      test_case: tc ?? { title: "(deleted test case)", steps: "", expected_result: "" },
    };
  });

  const project = run.projects as unknown as { name: string } | null;

  return (
    <div className="flex w-full flex-col gap-6">
      <div>
        <Link
          href={`/tests?project=${run.project_id}`}
          className="mb-1 inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="size-3" />
          Tests
        </Link>
        <h1 className="text-2xl font-semibold tracking-tight">
          Test run — {run.label || run.environment}
        </h1>
        <p className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
          {project?.name} · started {formatWhen(run.created_at)}
          <Badge variant="outline">{run.environment}</Badge>
          <Badge variant={run.status === "completed" ? "secondary" : "outline"}>
            {run.status}
          </Badge>
        </p>
      </div>

      <RunChecklist runId={run.id} runStatus={run.status} items={items} />
    </div>
  );
}
