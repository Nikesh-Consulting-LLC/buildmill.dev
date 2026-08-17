"use client";

// US-3.12 → US-13.10 → US-31.3 → US-55.1: a project is access, not a matrix.
// The per-project × run-kind grid is gone as a concept: what an agent DOES is
// agent-level only (`runner_config.enabled_kinds`, the us-53.4 checkboxes on
// its settings page), and a `worker_capabilities` row now only says WHICH
// projects it may work — exactly one 'access' row per (worker, project),
// migration 199. Projects inherit: on any project it can access, an agent
// does whatever its own checkboxes allow. Fail-closed stays (us-31.3): zero
// access rows means it can claim and clone nothing. Plain CRUD through RLS —
// enforcement is the SQL predicate `worker_has_grant`.

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Loader2 } from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { confirmDialog } from "@/components/ui/confirm-dialog";
import { AGENT_ROLES, roleLabelsForKinds } from "@/lib/agent-roles";
import { formatLastSeen } from "@/lib/format-time";
import { PrepareWorkspaceButton } from "./prepare-workspace-dialog";

// US-85.1: the latest preparation outcome per project, for the row caption.
type PrepSummary = {
  status: "queued" | "running" | "succeeded" | "failed";
  finished_at: string | null;
};

export type ProjectOption = { id: string; name: string };
export type AccessRow = { project_id: string };

export function ProjectAccess({
  workerId,
  orgId,
  principalId,
  projects,
  rows,
  isAgent = true,
}: {
  workerId: string;
  orgId: string;
  /** For the link to the settings page, where what-it-does is edited. */
  principalId: string;
  projects: ProjectOption[];
  rows: AccessRow[];
  /** A human worker has no `runner_config` / enabled-kinds concept — the
   * kinds line and its settings-page link only make sense for an agent. */
  isAgent?: boolean;
}) {
  const [accessed, setAccessed] = useState<Set<string>>(
    () => new Set(rows.map((r) => r.project_id))
  );
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // What this agent does, read off its own config — undefined while loading,
  // null = every kind (no row, or the column is null — us-53.4's no-backfill
  // rule), [] = benched. Read-only here: the checkboxes live on Settings.
  const [kinds, setKinds] = useState<string[] | null | undefined>(undefined);
  // US-85.1: latest workspace-preparation outcome per project, and whether
  // the runner is connected right now (no runner — nothing to prepare).
  const [preps, setPreps] = useState<Record<string, PrepSummary>>({});
  const [runnerLive, setRunnerLive] = useState(false);

  const loadPreps = useCallback(() => {
    if (!isAgent) return;
    const supabase = createClient();
    supabase
      .from("workspace_prep_jobs")
      .select("project_id, status, finished_at, created_at")
      .eq("worker_id", workerId)
      .order("created_at", { ascending: false })
      .limit(100)
      .then(({ data }) => {
        const latest: Record<string, PrepSummary> = {};
        for (const row of data ?? []) {
          if (!latest[row.project_id]) {
            latest[row.project_id] = {
              status: row.status as PrepSummary["status"],
              finished_at: row.finished_at,
            };
          }
        }
        setPreps(latest);
      });
  }, [workerId, isAgent]);

  useEffect(() => {
    loadPreps();
  }, [loadPreps]);

  useEffect(() => {
    if (!isAgent) return;
    let cancelled = false;
    const supabase = createClient();
    // us-116.4: presence is the `live_runner_sessions` view — connected AND
    // heartbeated inside the window — the one predicate every surface reads.
    supabase
      .from("live_runner_sessions")
      .select("id")
      .eq("worker_id", workerId)
      .limit(1)
      .then(({ data }) => {
        if (!cancelled) setRunnerLive((data ?? []).length > 0);
      });
    return () => {
      cancelled = true;
    };
  }, [workerId, isAgent]);

  useEffect(() => {
    if (!isAgent) return;
    let cancelled = false;
    const supabase = createClient();
    supabase
      .from("runner_config")
      .select("enabled_kinds")
      .eq("worker_id", workerId)
      .maybeSingle()
      .then(({ data }) => {
        if (!cancelled) setKinds((data?.enabled_kinds as string[] | null) ?? null);
      });
    return () => {
      cancelled = true;
    };
  }, [workerId, isAgent]);

  // US-77.1: read out as ROLES, the four checkboxes its settings page offers —
  // not the run kinds the column happens to store.
  const kindsLine =
    kinds === undefined
      ? "…"
      : kinds === null
        ? `every role (all ${AGENT_ROLES.length}: ${AGENT_ROLES.map((r) => r.label).join(", ")})`
        : kinds.length === 0
          ? "nothing — every role is unchecked in its settings"
          : roleLabelsForKinds(kinds).join(", ") || kinds.join(", ");

  async function toggle(projectId: string) {
    setError(null);
    const has = accessed.has(projectId);
    if (has && accessed.size === 1) {
      const ok = await confirmDialog({
        title: "Remove the last project?",
        description: isAgent
          ? "This is the agent's last project — removing access leaves it unable to claim or clone anything until a project is granted again."
          : "This is this person's last project — removing access leaves them unable to clone or fetch through the factory remote until a project is granted again.",
        confirmLabel: "Remove access",
        destructive: true,
      });
      if (!ok) return;
    }
    const supabase = createClient();
    setBusy(projectId);
    try {
      if (has) {
        // Everything for the pair, no capability filter — a legacy per-kind
        // row left behind would otherwise keep the project accessible.
        const { error: dbError } = await supabase
          .from("worker_capabilities")
          .delete()
          .eq("worker_id", workerId)
          .eq("project_id", projectId);
        if (dbError) {
          setError(dbError.message);
          return;
        }
        setAccessed((cur) => {
          const next = new Set(cur);
          next.delete(projectId);
          return next;
        });
      } else {
        const { error: dbError } = await supabase
          .from("worker_capabilities")
          .insert({
            org_id: orgId,
            worker_id: workerId,
            project_id: projectId,
            capability: "access",
          });
        if (dbError) {
          setError(dbError.message);
          return;
        }
        setAccessed((cur) => new Set(cur).add(projectId));
      }
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="grid gap-3">
      <p className="text-sm text-muted-foreground">
        {isAgent ? (
          <>
            On these projects this agent does:{" "}
            <span className="text-foreground">{kindsLine}</span>. What it does
            is edited on{" "}
            <Link
              href={`/team/${principalId}/settings`}
              className="underline underline-offset-4"
            >
              its settings page
            </Link>{" "}
            — access here only decides where.
          </>
        ) : (
          "Projects this person can git-clone/fetch through the factory remote, and claim work on over MCP."
        )}
      </p>

      {accessed.size === 0 && (
        <p className="text-sm text-muted-foreground">
          <Badge variant="secondary" className="mr-2 font-normal">
            No access — can do nothing
          </Badge>
          {/* US-31.3: the gate is fail-closed — zero access rows means no
              work and no repository access, not unrestricted. */}
          {isAgent
            ? "This agent can claim nothing — give it access to a project below."
            : "This person can't clone or fetch through the factory remote — give them access to a project below."}
        </p>
      )}

      {projects.length === 0 ? (
        <p className="text-xs text-muted-foreground">
          No projects in this organization yet.
        </p>
      ) : (
        <ul className="grid gap-1.5">
          {projects.map((p) => {
            const prep = preps[p.id];
            return (
              <li key={p.id}>
                <label className="flex cursor-pointer items-center gap-2 rounded-md border px-3 py-2 text-sm hover:border-ring/60">
                  {busy === p.id ? (
                    <Loader2 className="size-4 animate-spin text-muted-foreground" />
                  ) : (
                    <Checkbox
                      checked={accessed.has(p.id)}
                      disabled={busy !== null}
                      onCheckedChange={() => void toggle(p.id)}
                    />
                  )}
                  <span className="min-w-0 truncate font-medium">{p.name}</span>
                  {/* US-85.1: make the granted workspace ready before any run
                      needs it. Agents only — a person's IDE is their own. */}
                  {isAgent && accessed.has(p.id) && (
                    <span className="ml-auto flex shrink-0 items-center gap-2">
                      {prep && (
                        <span className="text-xs text-muted-foreground">
                          {prep.status === "succeeded"
                            ? `Prepared ${formatLastSeen(prep.finished_at)}`
                            : prep.status === "failed"
                              ? "Preparation failed"
                              : "Preparing…"}
                        </span>
                      )}
                      <PrepareWorkspaceButton
                        workerId={workerId}
                        projectId={p.id}
                        projectName={p.name}
                        runnerLive={runnerLive}
                        onFinished={loadPreps}
                      />
                    </span>
                  )}
                </label>
              </li>
            );
          })}
        </ul>
      )}

      {error && <p className="text-sm font-medium text-destructive">{error}</p>}
    </div>
  );
}
