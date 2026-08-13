"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "@/lib/router-with-progress";
import {
  Bot,
  Check,
  ChevronRight,
  Ellipsis,
  Loader2,
  MapPin,
  Pause,
  User,
  X,
} from "lucide-react";
import { apiFetch } from "@/lib/api";
import { cn } from "@/lib/utils";
import { createClient } from "@/lib/supabase/client";
import {
  deriveCompact,
  deriveTracker,
  routedPresetIds,
  type Stage,
  type StageState,
  type TrackerInput,
} from "@/lib/stage-tracker";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { StatusBadge, type IssueStatus } from "@/components/status-badge";
import { DispatchPreviewDialog } from "@/components/instruction-preview";
import {
  BatchPhaseDialog,
  fetchDispatchPhase,
  type DispatchPhase,
} from "@/components/batch-phase-dialog";
import {
  ActorIcon,
  STATE_STYLES,
  STATE_TITLES,
  StateIcon,
} from "@/components/stage-state";

const DOT_STYLES: Record<StageState, string> = {
  complete: "bg-emerald-600 dark:bg-emerald-500",
  "in-progress": "bg-blue-600 dark:bg-blue-500",
  waiting: "bg-amber-500",
  failed: "bg-red-600 dark:bg-red-500",
  "not-started": "bg-muted-foreground/25",
  "not-tracked": "bg-muted-foreground/25",
};

const LABEL_STYLES: Record<StageState, string> = {
  complete: "text-emerald-700 dark:text-emerald-400",
  "in-progress": "text-blue-700 dark:text-blue-400",
  waiting: "text-amber-700 dark:text-amber-400",
  failed: "text-red-700 dark:text-red-400",
  "not-started": "text-muted-foreground",
  "not-tracked": "text-muted-foreground",
};

function StagePill({ stage }: { stage: Stage }) {
  return (
    <span
      title={`${stage.label} · ${STATE_TITLES[stage.state]} · ${stage.actor === "agent" ? "agent" : "person"} works this stage`}
      className={cn(
        "flex min-w-0 flex-1 items-center justify-center gap-1.5 rounded-full px-2 py-1.5 text-xs font-medium",
        STATE_STYLES[stage.state]
      )}
    >
      <StateIcon state={stage.state} />
      <span className="truncate">{stage.label}</span>
      <ActorIcon actor={stage.actor} />
    </span>
  );
}

/** The full "Where it sits" card on work item detail (US-2.23).
 *
 * US-15.19: `variant="bar"` renders the same thing condensed to a single row
 * for the work item's sticky cockpit header — same stages, same realtime, and
 * the *same* single primary action (us-12.1), just without the card chrome and
 * the legend. It is a variant rather than a second component precisely so the
 * action can never be duplicated. */
export function StageTrackerCard({
  input,
  abandoned,
  displayId,
  variant = "card",
}: {
  input: TrackerInput;
  abandoned: boolean;
  /** US-7.10: the epic-scoped work-item id, shown on the tracker header. */
  displayId?: string | null;
  variant?: "card" | "bar";
}) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  // US-33.5: the presets this org has, and the manager's choice for this
  // dispatch. Empty selection means "the agent's own routes decide", which is
  // the default and stays the default.
  const [presets, setPresets] = useState<{ id: string; name: string }[]>([]);
  const [preset, setPreset] = useState("");
  const [error, setError] = useState<string | null>(null);
  /** US-49.1: the single-item dispatch awaiting confirmation, by action kind.
   * Null when no dialog is open. */
  const [pending, setPending] = useState<string | null>(null);
  /** US-27.11: the phase a batch dispatch would run, held while the manager
   * confirms it. Null when nothing is pending. */
  const [pendingPhase, setPendingPhase] = useState<DispatchPhase | null>(null);
  const { stages, context, action, waitingOn } = deriveTracker(input);

  // Every run transition mirrors onto the issue row (claim, callback,
  // dispatch), so the issue row alone is a sufficient live signal.
  useEffect(() => {
    const supabase = createClient();
    let channel: ReturnType<typeof supabase.channel> | null = null;
    let cancelled = false;

    async function subscribe() {
      const {
        data: { session },
      } = await supabase.auth.getSession();
      if (!session || cancelled) return;
      await supabase.realtime.setAuth(session.access_token);
      channel = supabase
        .channel(`stage-tracker-${input.issueId}`, { config: { private: false } })
        .on(
          "postgres_changes",
          {
            event: "UPDATE",
            schema: "public",
            table: "issues",
            filter: `id=eq.${input.issueId}`,
          },
          () => router.refreshSilently()
        )
        .subscribe();
    }

    subscribe();
    return () => {
      cancelled = true;
      if (channel) supabase.removeChannel(channel);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [input.issueId]);

  // US-55.5: scoped to THIS org and to how the agents assigned to this
  // project are actually configured — the override offers only presets some
  // active, project-granted agent's route points at. The unscoped read showed
  // every org the manager belongs to (seeded presets, doubled) plus every
  // kind's preset: sixteen options for one planning dispatch (2026-07-30),
  // and a doubled pick could stamp a foreign org's preset id on the run.
  // Any failure or empty step leaves the picker absent and dispatch behaving
  // exactly as it did before (US-33.5's rule).
  useEffect(() => {
    let cancelled = false;
    const supabase = createClient();
    (async () => {
      // US-57.7: presets are platform-authored now — the override decides
      // nothing an org member is still choosing between, so it hides for
      // everyone but the platform admin (who edits the routes themselves).
      const { data: isAdmin } = await supabase.rpc("is_platform_admin");
      if (!isAdmin || cancelled) return;

      const { data: issueRow } = await supabase
        .from("issues")
        .select("project_id")
        .eq("id", input.issueId)
        .single();
      const projectId = issueRow?.project_id;
      if (!projectId || cancelled) return;
      const { data: caps } = await supabase
        .from("worker_capabilities")
        .select("worker_id")
        .eq("project_id", projectId);
      const granted = [
        ...new Set((caps ?? []).map((c) => c.worker_id as string)),
      ];
      if (granted.length === 0 || cancelled) return;
      const { data: activeRows } = await supabase
        .from("workers")
        .select("id")
        .in("id", granted)
        .eq("status", "active");
      const active = (activeRows ?? []).map((w) => w.id as string);
      if (active.length === 0 || cancelled) return;
      const { data: configs } = await supabase
        .from("runner_config")
        .select("run_routes")
        .in("worker_id", active);
      const routed = routedPresetIds(configs ?? []);
      if (routed.length === 0 || cancelled) return;
      const { data } = await supabase
        .from("agent_presets")
        .select("id, name")
        .eq("org_id", input.orgId)
        .in("id", routed)
        .is("archived_at", null)
        .order("sort_order", { ascending: true });
      if (!cancelled && data) {
        setPresets(data as unknown as { id: string; name: string }[]);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [input.issueId, input.orgId]);

  /** US-12.1: the primary slot's three POST actions share one handler —
   * the endpoint is the only thing that differs, and routing them through
   * separate buttons scattered down the page is exactly what this story
   * removes. */
  async function post(path: string, body?: unknown) {
    setError(null);
    setBusy(true);
    try {
      await apiFetch(`/api/v1/issues/${input.issueId}${path}`, {
        method: "POST",
        ...(body
          ? {
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify(body),
            }
          : {}),
      });
      router.refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  /** US-27.11: what a batch dispatch is ABOUT to do — see BatchPhaseDialog,
   * which owns the confirm (US-84.1: the dashboard's feature header shares
   * it, so the two entry points cannot drift). */
  async function askPhase() {
    setError(null);
    setBusy(true);
    try {
      setPendingPhase(await fetchDispatchPhase(input.issueId));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  /** US-49.1: the three single-item dispatches confirm first, showing the
   * instructions the run will read. `null` asks the database which phase a
   * plain dispatch would run — the client never guesses it. */
  const PREVIEWED: Record<string, { path: string; kind: string | null }> = {
    dispatch: { path: "/dispatch", kind: null },
    "draft-prd": { path: "/prd/draft", kind: "prd" },
    breakdown: { path: "/breakdown/dispatch", kind: "breakdown" },
  };

  function confirmDispatch() {
    const step = pending ? PREVIEWED[pending] : null;
    if (!step) return;
    setPending(null);
    // US-33.5: the manager's choice for THIS dispatch, if they made one. It
    // becomes the top layer of the precedence us-32.7 resolves, so the run
    // itself records that a manager decided — not just what was decided.
    void post(
      step.path,
      step.path === "/dispatch" && preset ? { preset_id: preset } : undefined
    );
  }

  function runAction() {
    if (!action || action.disabled) return;
    if (PREVIEWED[action.kind]) return setPending(action.kind);
    // US-20.6: the feature-level pair. Same slot, same handler — the batch is
    // one action, not a second primary control competing with the first.
    // US-27.11: except the batch now states its phase first.
    if (action.kind === "batch-dispatch") return askPhase();
    if (action.kind === "approve-all-plans") return post("/plans/approve-all");
    if (action.kind === "curate-all") return curateAll();
  }

  /** US-41.2: move every draft story under this feature to `ready`.
   *
   * Straight to the RPC, like `askPhase` above — this is plain org-scoped
   * CRUD and RLS is the authorization, so it needs no API endpoint. One
   * transaction rather than N client updates, which could half-succeed and
   * leave the rail in a state nobody asked for. */
  async function curateAll() {
    setError(null);
    setBusy(true);
    try {
      const supabase = createClient();
      const { error: rpcError } = await supabase.rpc("curate_feature_stories", {
        p_feature: input.issueId,
      });
      if (rpcError) throw new Error(rpcError.message);
      router.refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  /** US-12.1's one primary control, shared by both variants so the bar can
   * never become a second button issuing the same POST. */
  const primaryAction = (
    <>
      {/* US-33.5: beside the button, not instead of it — the default stays
          "the agent's own routes decide", which is what makes this an override
          rather than a question the manager has to answer every time. */}
      {!abandoned && action?.kind === "dispatch" && presets.length > 0 && (
        <select
          value={preset}
          onChange={(e) => setPreset(e.target.value)}
          title="How this run should be executed. Unset uses the agent's own route for this kind."
          className="h-8 shrink-0 rounded-md border bg-background px-2 text-xs"
        >
          <option value="">The agent&apos;s own settings</option>
          {presets.map((p) => (
            <option key={p.id} value={p.id}>
              Run as {p.name}
            </option>
          ))}
        </select>
      )}
      {!abandoned && action?.kind === "link" && (
        <Button size="sm" className="shrink-0" render={<Link href={action.href} />}>
          {action.label}
        </Button>
      )}
      {!abandoned && action && action.kind !== "link" && (
        <Button
          size="sm"
          className="shrink-0"
          onClick={runAction}
          disabled={busy || !!action.disabled}
          title={action.reason ?? action.label}
        >
          {busy && <Loader2 className="size-3.5 animate-spin" />}
          {action.label}
        </Button>
      )}
      {/* US-49.1: what the agent will read, before it reads it. */}
      <DispatchPreviewDialog
        issueId={input.issueId}
        orgId={input.orgId}
        kind={pending ? PREVIEWED[pending].kind : null}
        open={!!pending}
        onOpenChange={(o) => !o && setPending(null)}
        onConfirm={confirmDispatch}
        busy={busy}
      />
      {/* US-27.11: say which phase this is before queueing anything. The two
          outcomes are "plan these stories" and "build these stories", and
          getting the wrong one costs six runs and a repair. */}
      <BatchPhaseDialog
        featureId={input.issueId}
        orgId={input.orgId}
        phase={pendingPhase}
        onOpenChange={(o) => !o && setPendingPhase(null)}
        onConfirm={() => {
          setPendingPhase(null);
          post("/batch-dispatch");
        }}
        busy={busy}
      />
      {/* US-22.10: the reason is readable, not only on hover — a greyed
          button with no visible explanation reads as a bug. */}
      {!abandoned && action?.disabled && action.reason && (
        <span className="text-xs text-muted-foreground">
          {action.reasonHref ? (
            <Link
              href={action.reasonHref}
              className="underline underline-offset-2 hover:text-foreground"
            >
              {action.reason}
            </Link>
          ) : (
            action.reason
          )}
        </span>
      )}
    </>
  );

  // US-15.19: the condensed row for the sticky cockpit header.
  if (variant === "bar") {
    return (
      <div className="flex flex-col gap-1.5">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
          <div
            className={cn(
              "flex min-w-0 flex-1 items-center gap-1",
              abandoned && "opacity-50 grayscale"
            )}
          >
            {stages.map((stage, i) => (
              <span key={stage.key} className="contents">
                {i > 0 && (
                  <ChevronRight className="size-3 shrink-0 text-muted-foreground" />
                )}
                <StagePill stage={stage} />
              </span>
            ))}
          </div>
          <div
            data-primary-action-slot
            className="flex shrink-0 items-center gap-2"
          >
            <p className="flex min-w-0 items-center gap-1.5 text-xs text-muted-foreground">
              {!abandoned && waitingOn === "factory" && (
                <Loader2 className="size-3 shrink-0 animate-spin text-blue-600 dark:text-blue-500" />
              )}
              <span className="min-w-0 truncate">
                {abandoned ? "Abandoned — the rail is frozen." : context}
              </span>
            </p>
            {primaryAction}
          </div>
        </div>
        {error && (
          <p className="text-xs font-medium text-destructive">{error}</p>
        )}
      </div>
    );
  }

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-4">
        <CardTitle className="flex items-center gap-2 text-base">
          <MapPin className="size-4 text-muted-foreground" />
          Where it sits
          {displayId && (
            <span className="font-mono text-xs font-normal text-muted-foreground">
              {displayId}
            </span>
          )}
        </CardTitle>
        <span className="flex items-center gap-2">
          {abandoned && <Badge variant="secondary">Abandoned</Badge>}
          <StatusBadge status={input.status as IssueStatus} />
        </span>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <div
          className={cn(
            "flex items-center gap-1",
            abandoned && "opacity-50 grayscale"
          )}
        >
          {stages.map((stage, i) => (
            <span key={stage.key} className="contents">
              {i > 0 && (
                <ChevronRight className="size-3 shrink-0 text-muted-foreground" />
              )}
              <StagePill stage={stage} />
            </span>
          ))}
        </div>
        {/* US-12.1: the one primary action, in a fixed slot. It never moves
            between stages and it always says something — an empty slot
            reads as "nothing to do" when the truth is usually "the factory
            is working". */}
        <div
          data-primary-action-slot
          className={cn(
            "flex flex-wrap items-center justify-between gap-3 rounded-md border px-3 py-2",
            !abandoned && waitingOn === "you"
              ? "border-amber-200 bg-amber-50/60 dark:border-amber-900 dark:bg-amber-950/40"
              : "bg-muted/30"
          )}
        >
          <p className="flex min-w-0 items-center gap-2 text-sm text-muted-foreground">
            {!abandoned && waitingOn === "factory" && (
              <Loader2 className="size-3.5 shrink-0 animate-spin text-blue-600 dark:text-blue-500" />
            )}
            <span className="min-w-0">
              {abandoned ? "Abandoned — the rail is frozen." : context}
            </span>
          </p>
          {primaryAction}
          {!abandoned && !action && (
            <span className="shrink-0 text-xs text-muted-foreground">
              {waitingOn === "factory"
                ? "Waiting on the agent"
                : "Nothing for you to do here"}
            </span>
          )}
        </div>
        {error && <p className="text-sm font-medium text-destructive">{error}</p>}
        <p className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-muted-foreground">
          <span className="inline-flex items-center gap-1">
            <Check className="size-3 text-emerald-600 dark:text-emerald-500" /> complete
          </span>
          <span className="inline-flex items-center gap-1">
            <Ellipsis className="size-3 text-blue-600 dark:text-blue-500" /> factory working
          </span>
          <span className="inline-flex items-center gap-1">
            <Pause className="size-3 text-amber-600 dark:text-amber-500" /> waiting on you
          </span>
          <span className="inline-flex items-center gap-1">
            <X className="size-3 text-red-600 dark:text-red-500" /> failed
          </span>
          <span className="inline-flex items-center gap-1">
            <Bot className="size-3" /> agent
          </span>
          <span className="inline-flex items-center gap-1">
            <User className="size-3" /> person
          </span>
        </p>
      </CardContent>
    </Card>
  );
}

/** Compact five-dot strip + current-stage label for board cards and list
 * rows (US-2.23). Derives from type + status alone (optionally the latest
 * run kind); actor icons are omitted at this size. */
export function StageDots({
  type,
  status,
  latestRunKind = null,
  className,
}: {
  type: string;
  status: string;
  latestRunKind?: "plan" | "code" | null;
  className?: string;
}) {
  const { stages } = deriveCompact(type, status, latestRunKind);
  const current =
    stages.find((s) =>
      ["in-progress", "waiting", "failed"].includes(s.state)
    ) ?? [...stages].reverse().find((s) => s.state === "complete");

  return (
    <span
      className={cn("inline-flex shrink-0 items-center gap-2", className)}
      title={stages
        .map((s) => `${s.label}: ${STATE_TITLES[s.state]}`)
        .join(" · ")}
    >
      <span className="inline-flex items-center gap-1">
        {stages.map((s) => (
          <span
            key={s.key}
            className={cn("size-2 rounded-full", DOT_STYLES[s.state])}
          />
        ))}
      </span>
      {current && (
        <span
          className={cn(
            "text-xs font-medium",
            LABEL_STYLES[current.state]
          )}
        >
          {current.label}
        </span>
      )}
    </span>
  );
}
