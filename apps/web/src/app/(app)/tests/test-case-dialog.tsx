"use client";

import { useEffect, useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import { Loader2, Pencil, Plus, Sparkles } from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import { apiFetch } from "@/lib/api";
import { cn } from "@/lib/utils";
import { DocumentsPanel } from "@/components/documents-panel";
import type { DocumentRow } from "@/lib/documents";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { MarkdownEditor } from "@/components/markdown-editor";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

export const CATALOG_TEST_TYPES = ["unit", "regression", "pre-release"];
export const ENVIRONMENTS = ["dev", "uat", "production"];

export type TestCaseFormData = {
  id: string;
  title: string;
  steps: string;
  expected_result: string;
  test_types: string[];
  environments: string[];
  issue_id: string | null;
  always_on_uat?: boolean;
  module_id?: string | null;
};

export type IssueOption = { id: string; title: string };
export type ModuleOption = { id: string; name: string };

function ToggleChips({
  options,
  selected,
  onToggle,
}: {
  options: string[];
  selected: string[];
  onToggle: (value: string) => void;
}) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {options.map((o) => (
        <button
          key={o}
          type="button"
          onClick={() => onToggle(o)}
          className={cn(
            "rounded-full border px-3 py-1 text-xs font-medium transition-colors",
            selected.includes(o)
              ? "border-transparent bg-primary text-primary-foreground"
              : "text-muted-foreground hover:text-foreground"
          )}
        >
          {o}
        </button>
      ))}
    </div>
  );
}

export function TestCaseDialog({
  orgId,
  projectId,
  issues,
  modules = [],
  testCase,
}: {
  orgId: string;
  projectId: string;
  issues: IssueOption[];
  modules?: ModuleOption[];
  testCase?: TestCaseFormData;
}) {
  const router = useRouter();
  const isEdit = !!testCase;
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState(testCase?.title ?? "");
  const [steps, setSteps] = useState(testCase?.steps ?? "");
  const [expected, setExpected] = useState(testCase?.expected_result ?? "");
  const [types, setTypes] = useState<string[]>(testCase?.test_types ?? []);
  const [customType, setCustomType] = useState("");
  const [environments, setEnvironments] = useState<string[]>(
    testCase?.environments ?? []
  );
  const [issueId, setIssueId] = useState<string>(testCase?.issue_id ?? "none");
  const [alwaysOnUat, setAlwaysOnUat] = useState(
    testCase?.always_on_uat ?? false
  );
  const [moduleId, setModuleId] = useState<string>(
    testCase?.module_id ?? "none"
  );
  const [saving, setSaving] = useState(false);
  const [elaborating, setElaborating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [docs, setDocs] = useState<DocumentRow[] | null>(null);

  // US-2.22: attachments on an existing test case, loaded when opened.
  useEffect(() => {
    if (!open || !testCase) return;
    const supabase = createClient();
    supabase
      .from("documents")
      .select("*")
      .eq("test_case_id", testCase.id)
      .eq("attached_to", "test-case")
      .order("created_at", { ascending: true })
      .then(({ data }) => setDocs((data ?? []) as DocumentRow[]));
  }, [open, testCase]);

  const typeOptions = Array.from(new Set([...CATALOG_TEST_TYPES, ...types]));

  function toggle(list: string[], value: string) {
    return list.includes(value)
      ? list.filter((v) => v !== value)
      : [...list, value];
  }

  async function handleElaborate() {
    if (!title.trim()) {
      setError("Write a rough description in the title first.");
      return;
    }
    setError(null);
    setElaborating(true);
    try {
      const linkedIssue = issues.find((t) => t.id === issueId);
      const result = await apiFetch("/api/v1/llm/elaborate-test", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          description: title.trim(),
          context: linkedIssue ? `Linked work item: ${linkedIssue.title}` : undefined,
          project_id: projectId,
        }),
      });
      if (result.title) setTitle(result.title);
      setSteps(result.steps ?? "");
      setExpected(result.expected_result ?? "");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setElaborating(false);
    }
  }

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (!title.trim()) {
      setError("Title is required.");
      return;
    }
    if (!environments.length) {
      setError("Pick at least one environment this test applies to.");
      return;
    }

    setSaving(true);
    const supabase = createClient();
    try {
      const values = {
        title: title.trim(),
        steps: steps.trim(),
        expected_result: expected.trim(),
        test_types: types,
        environments,
        issue_id: issueId === "none" ? null : issueId,
        always_on_uat: alwaysOnUat,
        module_id: moduleId === "none" ? null : moduleId,
      };

      const { error: dbError } = isEdit
        ? await supabase.from("test_cases").update(values).eq("id", testCase.id)
        : await supabase
            .from("test_cases")
            .insert({ ...values, org_id: orgId, project_id: projectId });
      if (dbError) {
        setError(dbError.message);
        return;
      }

      setOpen(false);
      if (!isEdit) {
        setTitle("");
        setSteps("");
        setExpected("");
        setTypes([]);
        setEnvironments([]);
        setIssueId("none");
      }
      router.refresh();
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger
        render={
          isEdit ? (
            <Button variant="ghost" size="icon-sm" />
          ) : (
            <Button variant="create" />
          )
        }
      >
        {isEdit ? (
          <Pencil className="size-4" />
        ) : (
          <>
            <Plus className="size-4" />
            New test case
          </>
        )}
      </DialogTrigger>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{isEdit ? "Edit test case" : "New test case"}</DialogTitle>
          <DialogDescription>
            Steps a human tester follows, and what they should observe.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSave} className="grid gap-4">
          <div className="grid gap-2">
            <Label htmlFor="tc-title">Title</Label>
            <div className="flex items-start gap-2">
              <Input
                id="tc-title"
                placeholder="Rough description is fine — Elaborate expands it"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
              />
              <Button
                type="button"
                variant="outline"
                onClick={handleElaborate}
                disabled={elaborating}
                title="Expand the description into steps and an expected result with your configured LLM"
              >
                {elaborating ? (
                  <Loader2 className="size-4 animate-spin" />
                ) : (
                  <Sparkles className="size-4" />
                )}
                Elaborate
              </Button>
            </div>
          </div>
          <div className="grid gap-2">
            <Label htmlFor="tc-steps">Steps</Label>
            <MarkdownEditor
              id="tc-steps"
              orgId={orgId}
              rows={5}
              placeholder={"1. Open…\n2. Click…\n\nMarkdown supported."}
              value={steps}
              onChange={setSteps}
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="tc-expected">Expected result</Label>
            <MarkdownEditor
              id="tc-expected"
              orgId={orgId}
              rows={2}
              placeholder="What a passing run looks like."
              value={expected}
              onChange={setExpected}
            />
          </div>
          <div className="grid gap-2">
            <Label>Test types</Label>
            <ToggleChips
              options={typeOptions}
              selected={types}
              onToggle={(v) => setTypes((prev) => toggle(prev, v))}
            />
            <div className="flex items-center gap-2">
              <Input
                placeholder="Add a custom type"
                value={customType}
                onChange={(e) => setCustomType(e.target.value)}
                className="h-7 max-w-44 text-xs"
              />
              <Button
                type="button"
                variant="outline"
                size="xs"
                disabled={!customType.trim()}
                onClick={() => {
                  const v = customType.trim().toLowerCase();
                  if (v && !types.includes(v)) setTypes((prev) => [...prev, v]);
                  setCustomType("");
                }}
              >
                <Plus className="size-3" />
                Add
              </Button>
            </div>
          </div>
          <div className="grid gap-2">
            <Label>Environments</Label>
            <ToggleChips
              options={ENVIRONMENTS}
              selected={environments}
              onToggle={(v) => setEnvironments((prev) => toggle(prev, v))}
            />
          </div>
          {modules.length > 0 && (
            <div className="grid gap-2">
              <Label htmlFor="tc-module">Module</Label>
              <Select
                items={[
                  { value: "none", label: "No module" },
                  ...modules.map((m) => ({ value: m.id, label: m.name })),
                ]}
                value={moduleId}
                onValueChange={(v) => {
                  if (typeof v === "string") setModuleId(v);
                }}
              >
                <SelectTrigger id="tc-module" className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">No module</SelectItem>
                  {modules.map((m) => (
                    <SelectItem key={m.id} value={m.id}>
                      {m.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )}
          <label className="flex cursor-pointer items-center gap-2 text-sm">
            <input
              type="checkbox"
              className="size-3.5 accent-primary"
              checked={alwaysOnUat}
              onChange={(e) => setAlwaysOnUat(e.target.checked)}
            />
            <span>
              Run on every release&apos;s UAT{" "}
              <span className="text-xs text-muted-foreground">
                — attaches to every release, not just its own work item&apos;s
              </span>
            </span>
          </label>
          <div className="grid gap-2">
            <Label htmlFor="tc-issue">Linked work item</Label>
            <Select
              items={[
                { value: "none", label: "No linked work item" },
                ...issues.map((t) => ({ value: t.id, label: t.title })),
              ]}
              value={issueId}
              onValueChange={(v) => {
                if (typeof v === "string") setIssueId(v);
              }}
            >
              <SelectTrigger id="tc-issue" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="none">No linked work item</SelectItem>
                {issues.map((t) => (
                  <SelectItem key={t.id} value={t.id}>
                    {t.title}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          {error && (
            <p className="text-sm font-medium text-destructive">{error}</p>
          )}
          <DialogFooter>
            <Button type="submit" disabled={saving}>
              {saving && <Loader2 className="size-4 animate-spin" />}
              {isEdit ? "Save changes" : "Create test case"}
            </Button>
          </DialogFooter>
        </form>
        {isEdit && testCase && docs !== null && (
          <div className="rounded-md border p-3">
            <DocumentsPanel
              orgId={orgId}
              projectId={projectId}
              target={{ attachedTo: "test-case", testCaseId: testCase.id }}
              initialDocs={docs}
              variant="plain"
              title="Documents"
              emptyTitle="No documents"
              emptyDescription="Attach reference files or expected screenshots for this test."
            />
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
