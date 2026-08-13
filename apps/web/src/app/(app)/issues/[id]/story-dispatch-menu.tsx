"use client";

import { useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import { ChevronDown, Hammer, Loader2, NotebookPen } from "lucide-react";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { toastError, toastSuccess } from "@/components/ui/toast";
import { DispatchPreviewDialog } from "@/components/instruction-preview";
import type { BuildMode } from "@/lib/stage-tracker";

/** Statuses a plan run is legal from — the mirror of migration 166's
 * `v_can_plan`. `planned` is the re-plan case. */
const PLANNABLE = ["draft", "ready", "failed", "needs-fixes", "planned"];

/** Statuses a code run is legal from, given an approved plan — migration 166's
 * `v_can_code`. `failed` belongs here per migration 146: a build that failed
 * goes back to building, not to planning. */
const CODEABLE = ["planned", "needs-fixes", "failed"];

export type StoryDispatchState = {
  /** US-49.1: the preview audits an instruction edit against it. */
  orgId: string;
  status: string;
  hasApprovedPlan: boolean;
  /** The story's display id, for the toast. Falls back to the title. */
  label: string;
  /** Feature/epic mode means the FEATURE owns the code build (us-22.10). */
  buildMode: BuildMode;
  /** Display id of the feature that owns the build, for the refusal text. */
  featureLabel: string;
  /** Sequential build mode: another non-terminal issue in this project, if
   * any — while it exists, dispatch_issue refuses both plan and code for
   * every other issue in the project. */
  blockingIssue: { id: string; label: string } | null;
};

/** Sequential build mode's refusal — checked before any status-specific
 * reason, since it's a project-wide condition rather than this story's own
 * state (mirrors dispatch_issue, which checks it first too). */
function sequentialRefusal(s: StoryDispatchState): string | null {
  if (!s.blockingIssue) return null;
  return `${s.blockingIssue.label} must reach merged first (sequential mode)`;
}

/** Why this phase can't run from here, or null when it can.
 *
 * These predicates are the client-side echo of the RPC's; the RPC stays the
 * authority and its refusal still surfaces as a toast. They exist so the menu
 * can say why in advance rather than letting the manager find out by pressing
 * a button and reading an error. */
function planRefusal(s: StoryDispatchState): string | null {
  const sequential = sequentialRefusal(s);
  if (sequential) return sequential;
  if (PLANNABLE.includes(s.status)) return null;
  if (s.status === "queued" || s.status === "running")
    return "A run is already in flight";
  return `Not from ${s.status}`;
}

function codeRefusal(s: StoryDispatchState): string | null {
  const sequential = sequentialRefusal(s);
  if (sequential) return sequential;
  if (!s.hasApprovedPlan) return "Needs an approved plan first";
  if (!CODEABLE.includes(s.status)) {
    if (s.status === "queued" || s.status === "running")
      return "A run is already in flight";
    return `Not from ${s.status}`;
  }
  // us-22.10: the feature owns the build unless this story broke out of the
  // batch by failing or being sent back.
  if (
    s.buildMode !== "story" &&
    !["failed", "needs-fixes"].includes(s.status)
  ) {
    return `${s.featureLabel} owns the build`;
  }
  return null;
}

/** Send ONE story to planning or to coding, named explicitly.
 *
 * The feature's own rail dispatches the whole batch and infers the phase from
 * the stories' rolled-up state. That is the right default and it stays. This is
 * the per-story escape hatch beside it: re-plan the one story whose plan came
 * back wrong, or build the one that failed, without hand-editing statuses or
 * taking the other five along. The chosen phase goes to the API as `kind`, so
 * what the menu says is what runs — never an inference that disagrees with the
 * label the manager clicked. */
export function StoryDispatchMenu({
  issueId,
  state,
}: {
  issueId: string;
  state: StoryDispatchState;
}) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  /** US-49.1: the phase the manager chose, held while they read what the
   * agent will be given. The menu names the kind, so the preview shows that
   * kind rather than asking the database to infer one. */
  const [pending, setPending] = useState<"plan" | "code" | null>(null);
  /** US-49.7: draw instead of plan. Same option, same wording, whether one
   * story is dispatched or all of them. */
  const [draw, setDraw] = useState(false);
  const planWhy = planRefusal(state);
  const codeWhy = codeRefusal(state);

  async function dispatch(kind: "plan" | "code") {
    const drawing = draw && kind === "plan";
    setPending(null);
    setBusy(true);
    try {
      if (drawing) {
        await apiFetch(`/api/v1/issues/${issueId}/wireframe/dispatch`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({}),
        });
        toastSuccess(
          `Drawing ${state.label}`,
          "Queued — plan it once the drawing lands."
        );
      } else {
        await apiFetch(`/api/v1/issues/${issueId}/dispatch`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ kind }),
        });
        toastSuccess(
          kind === "plan"
            ? `Planning ${state.label}`
            : `Building ${state.label}`,
          "Queued — it starts as soon as an agent picks it up."
        );
      }
      setDraw(false);
      router.refresh();
    } catch (e) {
      toastError(
        drawing
          ? "Couldn't draw it"
          : kind === "plan"
            ? "Couldn't plan it"
            : "Couldn't build it",
        (e as Error).message
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <DispatchPreviewDialog
        issueId={issueId}
        orgId={state.orgId}
        kind={pending}
        open={!!pending}
        onOpenChange={(o) => {
          if (!o) {
            setPending(null);
            setDraw(false);
          }
        }}
        onConfirm={() => pending && dispatch(pending)}
        busy={busy}
        wireframe={
          pending === "plan"
            ? {
                checked: draw,
                onChange: setDraw,
                note: "One drawing run for this story. Plan it once the drawing lands.",
              }
            : undefined
        }
      />
    <DropdownMenu>
      <DropdownMenuTrigger
        render={
          <Button
            variant="outline"
            size="sm"
            className="h-7 shrink-0 px-2 text-xs"
            title="Send this story to planning or to coding"
          />
        }
      >
        {busy ? (
          <Loader2 className="size-3.5 animate-spin" />
        ) : (
          <ChevronDown className="size-3.5" />
        )}
        Dispatch
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-auto min-w-60">
        {/* DropdownMenuLabel is Base UI's Menu.GroupLabel, which throws
            (production error #31) unless a Menu.Group owns it. Every other
            menu in the app wraps it; this one did not, and crashed the page
            on the mouse-down that opened it. */}
        <DropdownMenuGroup>
          <DropdownMenuLabel>Dispatch this story</DropdownMenuLabel>
        </DropdownMenuGroup>
        <DropdownMenuItem
          disabled={busy || !!planWhy}
          onClick={() => setPending("plan")}
        >
          <NotebookPen />
          <span className="flex flex-col gap-0.5 py-0.5">
            <span>{state.hasApprovedPlan ? "Re-plan it" : "Plan it"}</span>
            <span className="text-xs text-muted-foreground">
              {planWhy ??
                (state.hasApprovedPlan
                  ? "Writes a new plan over the approved one"
                  : "Writes the plan for this story alone")}
            </span>
          </span>
        </DropdownMenuItem>
        <DropdownMenuItem
          disabled={busy || !!codeWhy}
          onClick={() => setPending("code")}
        >
          <Hammer />
          <span className="flex flex-col gap-0.5 py-0.5">
            <span>Code it</span>
            <span className="text-xs text-muted-foreground">
              {codeWhy ?? "Builds this story on its own branch"}
            </span>
          </span>
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
    </>
  );
}
