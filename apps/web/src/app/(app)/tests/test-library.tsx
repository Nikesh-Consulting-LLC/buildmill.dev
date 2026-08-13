"use client";

import { useMemo, useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import {
  Archive,
  ArchiveRestore,
  Bot,
  FlaskConical,
  Loader2,
  User,
  Zap,
} from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { EmptyState } from "@/components/empty-state";
import { BulkDeleteBar } from "@/components/bulk-delete-bar";
import { FilterPill, FILTER_PILL_ANY as ANY } from "@/components/filter-pill";
import {
  CATALOG_TEST_TYPES,
  ENVIRONMENTS,
  TestCaseDialog,
  type IssueOption,
  type ModuleOption,
} from "./test-case-dialog";

export type TestCaseRow = {
  id: string;
  title: string;
  steps: string;
  expected_result: string;
  source: string;
  status: string;
  test_types: string[];
  environments: string[];
  issue_id: string | null;
  execution: string;
  suite_id: string | null;
  spec_ref: string | null;
  always_on_uat: boolean;
  module_id: string | null;
};

export type SuiteOption = { id: string; name: string };

export function TestLibrary({
  orgId,
  projectId,
  testCases,
  issues,
  suites,
  modules,
}: {
  orgId: string;
  projectId: string;
  testCases: TestCaseRow[];
  issues: IssueOption[];
  suites: SuiteOption[];
  modules: ModuleOption[];
}) {
  const router = useRouter();
  const [type, setType] = useState(ANY);
  const [environment, setEnvironment] = useState(ANY);
  const [source, setSource] = useState(ANY);
  const [status, setStatus] = useState("active");
  const [issue, setIssue] = useState(ANY);
  const [execution, setExecution] = useState(ANY);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());
  const [automating, setAutomating] = useState(false);
  const [automateError, setAutomateError] = useState<string | null>(null);

  const issueTitle = useMemo(
    () => new Map(issues.map((t) => [t.id, t.title])),
    [issues]
  );
  const allTypes = useMemo(
    () =>
      Array.from(
        new Set([...CATALOG_TEST_TYPES, ...testCases.flatMap((c) => c.test_types)])
      ),
    [testCases]
  );

  const suiteName = useMemo(
    () => new Map(suites.map((s) => [s.id, s.name])),
    [suites]
  );

  const visible = testCases.filter(
    (c) =>
      (status === ANY || c.status === status) &&
      (type === ANY || c.test_types.includes(type)) &&
      (environment === ANY || c.environments.includes(environment)) &&
      (source === ANY || c.source === source) &&
      (issue === ANY || c.issue_id === issue) &&
      (execution === ANY || c.execution === execution)
  );

  async function setCaseStatus(id: string, next: "active" | "abandoned") {
    setBusyId(id);
    const supabase = createClient();
    await supabase.from("test_cases").update({ status: next }).eq("id", id);
    setBusyId(null);
    router.refresh();
  }

  // Deletion only ever targets currently visible selected rows (US-2.26).
  const selectedVisible = visible.filter((c) => selected.has(c.id));
  const allSelected =
    visible.length > 0 && visible.every((c) => selected.has(c.id));
  const someSelected = visible.some((c) => selected.has(c.id));

  function toggleSelected(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  // US-82.2: prose cases become specs — one click creates a work item that
  // dispatches through the ordinary plan→code pipeline. The brief carries
  // the case bodies and ids; the agent writes the specs and reports the
  // links with report_spec_map, and the cases flip to automated ON MERGE.
  const automatable = selectedVisible.filter(
    (c) => c.execution === "manual" && c.status === "active"
  );

  async function handleAutomate() {
    if (!automatable.length) return;
    setAutomating(true);
    setAutomateError(null);
    const supabase = createClient();
    const suiteLines = suites.length
      ? suites.map((s) => `- ${s.name} (suite_id: ${s.id})`).join("\n")
      : "- (no suites declared yet — declare one in project settings → Suites first)";
    const caseBlocks = automatable
      .map(
        (c) =>
          `### Case ${c.id}\n**${c.title}**\n\nSteps:\n${c.steps || "(none written)"}\n\nExpected: ${c.expected_result || "(none written)"}`
      )
      .join("\n\n");
    const body =
      `Convert the ${automatable.length} manual test case(s) below into ` +
      `automated specs in this repo, one spec per case, under the target ` +
      `suite's convention so each spec's JUnit identity (spec_ref) is ` +
      `stable. Report the links with report_spec_map({test_case_id, ` +
      `suite_id, spec_ref}) before submitting — the cases flip to ` +
      `automated when this merges. Do not edit the cases themselves.\n\n` +
      `Declared suites:\n${suiteLines}\n\n${caseBlocks}`;
    const { data, error } = await supabase
      .from("issues")
      .insert({
        org_id: orgId,
        project_id: projectId,
        title: `Automate tests: ${automatable.length} case${automatable.length === 1 ? "" : "s"}`,
        type: "chore",
        body,
        acceptance_criteria: [
          "Every listed case has a spec file in the repo whose JUnit identity was reported via report_spec_map.",
          "The specs follow the target suite's conventions and read SF_BASE_URL for the instance under test.",
        ],
      })
      .select("id")
      .single();
    setAutomating(false);
    if (error) {
      setAutomateError(error.message);
      return;
    }
    setSelected(new Set());
    router.push(`/issues/${data.id}`);
  }

  async function handleBulkDelete() {
    const ids = selectedVisible.map((c) => c.id);
    const supabase = createClient();
    const { data, error } = await supabase
      .from("test_cases")
      .delete()
      .in("id", ids)
      .select("id");
    if (error) throw new Error(error.message);
    const deletedCount = data?.length ?? 0;
    setSelected(new Set());
    router.refresh();
    if (deletedCount < ids.length) {
      throw new Error(
        `Only ${deletedCount} of ${ids.length} test cases were deleted — refresh and retry.`
      );
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-1.5">
        <FilterPill
          label="Any type"
          value={type}
          onChange={setType}
          options={allTypes.map((t) => ({ value: t, label: t }))}
        />
        <FilterPill
          label="Any environment"
          value={environment}
          onChange={setEnvironment}
          options={ENVIRONMENTS.map((e) => ({ value: e, label: e }))}
        />
        <FilterPill
          label="Any source"
          value={source}
          onChange={setSource}
          options={[
            { value: "human", label: "human" },
            { value: "agent", label: "agent" },
          ]}
        />
        <FilterPill
          label="Any execution"
          value={execution}
          onChange={setExecution}
          options={[
            { value: "manual", label: "manual" },
            { value: "automated", label: "automated" },
          ]}
        />
        <FilterPill
          label="Any status"
          value={status}
          onChange={setStatus}
          options={[
            { value: "active", label: "active" },
            { value: "abandoned", label: "abandoned" },
          ]}
        />
        {issues.length > 0 && (
          <FilterPill
            label="Any work item"
            value={issue}
            onChange={setIssue}
            options={issues.map((t) => ({ value: t.id, label: t.title }))}
          />
        )}
      </div>

      {visible.length > 0 && (
        <label className="flex w-fit cursor-pointer items-center gap-1.5 text-xs text-muted-foreground">
          <Checkbox
            checked={allSelected}
            indeterminate={someSelected && !allSelected}
            onCheckedChange={() =>
              setSelected(
                allSelected ? new Set() : new Set(visible.map((c) => c.id))
              )
            }
            aria-label="Select all visible test cases"
          />
          Select all
        </label>
      )}

      {automatable.length > 0 && (
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={automating}
            onClick={handleAutomate}
            title="Create a work item that converts the selected manual cases into automated specs"
          >
            {automating ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <Zap className="size-4" />
            )}
            Automate {automatable.length} case
            {automatable.length === 1 ? "" : "s"}
          </Button>
          {automateError && (
            <p className="text-sm font-medium text-destructive">
              {automateError}
            </p>
          )}
        </div>
      )}

      <BulkDeleteBar
        count={selectedVisible.length}
        onClear={() => setSelected(new Set())}
        confirmTitle={`Delete ${selectedVisible.length} test case${selectedVisible.length === 1 ? "" : "s"}?`}
        confirmDescription={`This permanently deletes ${selectedVisible.length} test case${selectedVisible.length === 1 ? "" : "s"} and every result recorded for them in past test runs. Abandon instead if you want to keep run history. This can't be undone.`}
        confirmLabel="Delete test cases"
        onDelete={handleBulkDelete}
      />

      {!visible.length ? (
        <EmptyState
          icon={FlaskConical}
          title="No test cases match"
          description="Create one, loosen the filters, or let a run contribute some."
        />
      ) : (
        <ul className="grid gap-3">
          {visible.map((c) => (
            <li
              key={c.id}
              className={cn(
                "rounded-lg border p-4",
                c.status === "abandoned" && "opacity-60"
              )}
            >
              <div className="flex items-start justify-between gap-3">
                <Checkbox
                  className="mt-1"
                  checked={selected.has(c.id)}
                  onCheckedChange={() => toggleSelected(c.id)}
                  aria-label={`Select ${c.title}`}
                />
                <div className="min-w-0 flex-1">
                  <p className="font-medium">{c.title}</p>
                  <div className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
                    <span className="inline-flex items-center gap-1">
                      {c.source === "agent" ? (
                        <Bot className="size-3.5" />
                      ) : (
                        <User className="size-3.5" />
                      )}
                      {c.source}
                    </span>
                    {c.execution === "automated" && (
                      <Badge
                        variant="secondary"
                        title={c.spec_ref ?? undefined}
                        className="gap-1"
                      >
                        <Zap className="size-3" />
                        {c.suite_id
                          ? (suiteName.get(c.suite_id) ?? "automated")
                          : "automated"}
                      </Badge>
                    )}
                    {c.always_on_uat && (
                      <Badge variant="outline" title="Attached to every release's UAT test set">
                        every UAT
                      </Badge>
                    )}
                    {c.test_types.map((t) => (
                      <Badge key={t} variant="secondary">
                        {t}
                      </Badge>
                    ))}
                    {c.environments.map((e) => (
                      <Badge key={e} variant="outline">
                        {e}
                      </Badge>
                    ))}
                    {c.issue_id && issueTitle.get(c.issue_id) && (
                      <span className="truncate">
                        · issue: {issueTitle.get(c.issue_id)}
                      </span>
                    )}
                    {c.status === "abandoned" && (
                      <Badge variant="outline">abandoned</Badge>
                    )}
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-1">
                  <TestCaseDialog
                    orgId={orgId}
                    projectId={projectId}
                    issues={issues}
                    modules={modules}
                    testCase={c}
                  />
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    disabled={busyId === c.id}
                    title={
                      c.status === "active"
                        ? "Abandon — hide from lists and new runs, keep history"
                        : "Restore to active"
                    }
                    onClick={() =>
                      setCaseStatus(
                        c.id,
                        c.status === "active" ? "abandoned" : "active"
                      )
                    }
                  >
                    {c.status === "active" ? (
                      <Archive className="size-4" />
                    ) : (
                      <ArchiveRestore className="size-4" />
                    )}
                  </Button>
                </div>
              </div>
              {(c.steps || c.expected_result) && (
                <details className="mt-2">
                  <summary className="cursor-pointer select-none text-xs text-muted-foreground">
                    Steps & expected result
                  </summary>
                  <div className="mt-2 grid gap-2 text-sm">
                    {c.steps && (
                      <pre className="rounded-md bg-muted/50 p-3 text-xs leading-5 whitespace-pre-wrap">
                        {c.steps}
                      </pre>
                    )}
                    {c.expected_result && (
                      <p className="text-muted-foreground">
                        <span className="font-medium text-foreground">
                          Expected:{" "}
                        </span>
                        {c.expected_result}
                      </p>
                    )}
                  </div>
                </details>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
