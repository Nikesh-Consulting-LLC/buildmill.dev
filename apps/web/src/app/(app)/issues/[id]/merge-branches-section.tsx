"use client";

import { useCallback, useEffect, useState } from "react";
import { Loader2, GitMerge, AlertCircle } from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import { apiCall, apiFetch, ApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";

/** US-98.2: the branches a merge run will land.
 *
 * A merge is the one kind of work whose subject is not derivable from the
 * work item — which branches to land is the manager's decision, and until
 * this section existed nothing in the product could express it.
 *
 * The list is offered from the repository's real branches rather than typed,
 * so a name that does not exist cannot be entered in the first place. The
 * head sha beside each one is what the agent will actually be given: the
 * dispatch re-resolves them at the moment it runs (they are frozen into the
 * run's context there, not here), so what is shown is current rather than
 * whatever was true when the branch was picked.
 *
 * This is deliberately the minimum that works — us-98.2 AC5. The fuller
 * picker (grouping by work item, suggesting a set) is a separate decision.
 */
export function MergeBranchesSection({
  issueId,
  projectId,
  status,
}: {
  issueId: string;
  projectId: string;
  status: string;
}) {
  const [selected, setSelected] = useState<string[] | null>(null);
  const [available, setAvailable] = useState<
    { name: string; commit_sha: string }[] | null
  >(null);
  const [repo, setRepo] = useState<string | null>(null);
  const [defaultBranch, setDefaultBranch] = useState<string>("main");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [dispatching, setDispatching] = useState(false);
  const [dispatched, setDispatched] = useState(false);

  const load = useCallback(async () => {
    const supabase = createClient();
    const { data: issue } = await supabase
      .from("issues")
      .select("merge_branches, project_id")
      .eq("id", issueId)
      .maybeSingle();
    setSelected(((issue?.merge_branches as string[] | null) ?? []).slice());

    const { data: project } = await supabase
      .from("projects")
      .select("repo_full_name, default_branch")
      .eq("id", projectId)
      .maybeSingle();
    const full = project?.repo_full_name ?? null;
    setRepo(full);
    setDefaultBranch(project?.default_branch ?? "main");

    if (full && full.includes("/")) {
      try {
        const rows = await apiFetch(`/api/v1/github/repos/${full}/branches`);
        setAvailable(rows ?? []);
      } catch {
        // A repo we cannot list is not a broken page — the section says so
        // and the manager can still see what is already selected.
        setAvailable([]);
      }
    } else {
      setAvailable([]);
    }
  }, [issueId, projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function toggle(branch: string, on: boolean) {
    if (selected === null) return;
    const next = on
      ? [...selected, branch]
      : selected.filter((b) => b !== branch);
    const previous = selected;
    setSelected(next); // optimistic
    setBusy(branch);
    setError(null);
    const supabase = createClient();
    const { error: e } = await supabase
      .from("issues")
      .update({ merge_branches: next })
      .eq("id", issueId);
    setBusy(null);
    if (e) {
      setSelected(previous); // the trigger refused it — say so, don't pretend
      setError(e.message);
    }
  }

  async function dispatchMerge() {
    setDispatching(true);
    setError(null);
    try {
      await apiCall(`/api/v1/issues/${issueId}/dispatch-merge`, {
        method: "POST",
      });
      setDispatched(true);
    } catch (e) {
      setError(
        e instanceof ApiError || e instanceof Error
          ? e.message
          : "could not dispatch the merge",
      );
    } finally {
      setDispatching(false);
    }
  }

  if (selected === null || available === null) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Branches to merge</CardTitle>
        </CardHeader>
        <CardContent>
          <Loader2 className="size-4 animate-spin text-muted-foreground" />
        </CardContent>
      </Card>
    );
  }

  // The default branch is the thing being merged INTO — the database refuses
  // it, and offering it here would only teach that the hard way.
  const offerable = available.filter((b) => b.name !== defaultBranch);
  const shaFor = (name: string) =>
    available.find((b) => b.name === name)?.commit_sha?.slice(0, 7);
  const dispatchable = ["draft", "ready", "failed", "needs-fixes"].includes(
    status,
  );

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between gap-3">
          <CardTitle className="text-base">Branches to merge</CardTitle>
          <Button
            size="sm"
            disabled={
              selected.length === 0 || dispatching || dispatched || !dispatchable
            }
            onClick={dispatchMerge}
          >
            {dispatching ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <GitMerge className="size-4" />
            )}
            {dispatched ? "Dispatched" : "Merge them"}
          </Button>
        </div>
        <p className="text-sm text-muted-foreground">
          {selected.length === 0
            ? `Pick the branches to land onto ${defaultBranch}. A chore with no branches builds instead of merging.`
            : `${selected.length} branch${selected.length === 1 ? "" : "es"} will land onto ${defaultBranch}, all of them or none.`}
        </p>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {error ? (
          <div className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive">
            <AlertCircle className="mt-0.5 size-4 shrink-0" />
            <span>{error}</span>
          </div>
        ) : null}

        {!repo ? (
          <p className="text-sm text-muted-foreground">
            This project has no GitHub repository linked, so there are no
            branches to offer.
          </p>
        ) : offerable.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No branches beyond {defaultBranch} — nothing to merge.
          </p>
        ) : (
          <ul className="flex flex-col gap-1">
            {offerable.map((b) => {
              const on = selected.includes(b.name);
              return (
                <li
                  key={b.name}
                  className="flex items-center gap-3 rounded-md px-2 py-1.5 hover:bg-muted/50"
                >
                  <Checkbox
                    checked={on}
                    disabled={busy === b.name || dispatched}
                    onCheckedChange={(v) => toggle(b.name, v === true)}
                  />
                  <span className="min-w-0 flex-1 truncate font-mono text-sm">
                    {b.name}
                  </span>
                  <span className="shrink-0 font-mono text-xs text-muted-foreground">
                    {b.commit_sha.slice(0, 7)}
                  </span>
                </li>
              );
            })}
          </ul>
        )}

        {/* A branch that was listed and has since been deleted still counts —
            it is what the dispatch will refuse by name, so it must be visible
            here rather than only in that error. */}
        {selected.filter((b) => !available.some((a) => a.name === b)).length >
        0 ? (
          <div className="flex items-start gap-2 rounded-md border border-amber-500/40 bg-amber-500/5 p-3 text-sm">
            <AlertCircle className="mt-0.5 size-4 shrink-0 text-amber-600" />
            <div>
              <p className="font-medium">
                Selected, but not on the repository any more
              </p>
              <p className="text-muted-foreground">
                {selected
                  .filter((b) => !available.some((a) => a.name === b))
                  .join(", ")}{" "}
                — the dispatch will refuse until these are removed.
              </p>
            </div>
          </div>
        ) : null}

        {selected.length > 0 && shaFor(selected[0]) ? (
          <p className="text-xs text-muted-foreground">
            Heads are re-resolved at dispatch and frozen onto the run, so an
            agent always merges what the branch held when it started.
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}
