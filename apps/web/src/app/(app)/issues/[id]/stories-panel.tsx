import Link from "next/link";
import { Ban, BookOpen, Loader2, PencilRuler, Plus } from "lucide-react";
import type { BuildMode, ChildRollup } from "@/lib/stage-tracker";
import { ApproveAllPlansButton } from "@/components/approve-all-plans-button";
import { ComplexityBadge } from "@/components/complexity-badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { StatusBadge, type IssueStatus } from "@/components/status-badge";
import { IssueDialog, type EpicOption } from "../issue-dialog";
import { BreakdownPanel } from "./breakdown-panel";
import { StoryDispatchMenu } from "./story-dispatch-menu";
import { BulkDispatchButton } from "./bulk-dispatch-button";
import { DrawStoriesButton } from "./draw-stories-button";

export type ChildIssue = {
  id: string;
  title: string;
  status: string;
  updated_at: string;
  item_no: number | null;
  sub_no: number | null;
  /** US-7.1's advisory estimate, null until the story has been scored. */
  complexity: string | null;
  /** The epic-scoped story id (`US-1.1.3`), null when the numbering is
   * incomplete — a story created before us-7.10, or one whose epic has no
   * number. */
  displayId: string | null;
  /** Whether a code run is even possible for this story. */
  hasApprovedPlan: boolean;
  /** US-48.3: where this story stands on being drawn. `no-ui` is an answer
   * an agent gave, not a gap — it reads differently from `none`. */
  wireframe: "drawn" | "no-ui" | "in-flight" | "none";
};

const DONE_STATUSES = new Set(["merged", "done"]);

/** One glyph per story saying whether its screen exists. Deliberately quiet:
 * a story that has not been drawn shows nothing at all rather than a chip
 * reading "not drawn" on every row of an undrawn feature. */
function WireframeMark({ state }: { state: ChildIssue["wireframe"] }) {
  if (state === "none") return null;
  const [Icon, label, className] =
    state === "drawn"
      ? [PencilRuler, "Drawn", "text-muted-foreground"]
      : state === "in-flight"
        ? [Loader2, "An agent is drawing this", "text-muted-foreground animate-spin"]
        : [Ban, "No user-visible surface", "text-muted-foreground/60"];
  return <Icon className={`size-3.5 shrink-0 ${className}`} aria-label={label} />;
}

/** Stories section on a feature's detail page (us-2.4): breakdown when
 * there are no children yet, otherwise the child list with progress and
 * an Add story action. */
export function StoriesPanel({
  orgId,
  projectId,
  featureId,
  featureStatus,
  epicId,
  epics,
  childIssues,
  breakdownMode,
  breakdownInstructions,
  breakdownPending = false,
  rollup = null,
  buildMode = "story",
  featureLabel,
  featureTitle,
  blockingIssue = null,
}: {
  orgId: string;
  projectId: string;
  featureId: string;
  featureStatus: string;
  epicId: string | null;
  epics: EpicOption[];
  childIssues: ChildIssue[];
  breakdownMode?: string;
  breakdownInstructions?: string;
  breakdownPending?: boolean;
  /** US-20.6: present only in feature/epic build mode. */
  rollup?: ChildRollup | null;
  /** Decides whether a single story may be built on its own (us-22.10). */
  buildMode?: BuildMode;
  /** This feature's display id (`FEAT-1.1`), for the "owns the build" reason. */
  featureLabel: string;
  /** This feature's title, so a child story's breadcrumb can return here. */
  featureTitle: string;
  /** Sequential build mode: the project's earliest non-terminal issue other
   * than these children, if any — holds every child's dispatch alike. */
  blockingIssue?: { id: string; label: string } | null;
}) {
  const backTo = `from=${encodeURIComponent(`/issues/${featureId}`)}&fromLabel=${encodeURIComponent(featureTitle)}`;
  const done = childIssues.filter((c) => DONE_STATUSES.has(c.status)).length;

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0">
        <div className="space-y-1.5">
          <CardTitle className="flex items-center gap-2 text-base">
            <BookOpen className="size-4 text-muted-foreground" />
            Stories
          </CardTitle>
          <CardDescription>
            {childIssues.length
              ? `${done} of ${childIssues.length} done`
              : "Break this feature into engineering stories."}
          </CardDescription>
        </div>
        {childIssues.length > 0 && (
          <div className="flex items-center gap-2">
            {/* US-49.7: the batch, offered where the batch is. */}
            <BulkDispatchButton
              featureId={featureId}
              orgId={orgId}
              projectId={projectId}
            />
            <IssueDialog
              orgId={orgId}
              projectId={projectId}
              epics={epics}
              parent={{ id: featureId, epicId }}
              trigger={
                <Button variant="outline" size="sm">
                  <Plus className="size-4" />
                  Add story
                </Button>
              }
            />
          </div>
        )}
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {/* US-20.6: where the batch is, and the one action that clears the
            plan gate for all of it. The rail carries the dispatch actions;
            this is the review side. */}
        {rollup && rollup.total > 0 && (
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border bg-muted/40 px-3 py-2">
            <p className="text-xs text-muted-foreground">
              {rollup.curated < rollup.total ? (
                <>{rollup.total - rollup.curated} still in draft · </>
              ) : null}
              {rollup.planApproved} of {rollup.total} plans approved ·{" "}
              {rollup.merged} merged
              {rollup.inFlightPosition ? (
                <> · story {rollup.inFlightPosition} in flight</>
              ) : null}
              {rollup.troubled ? (
                <>
                  {" "}
                  ·{" "}
                  <Link
                    href={`/issues/${rollup.troubled.id}?${backTo}`}
                    className="font-medium text-destructive underline-offset-4 hover:underline"
                  >
                    {rollup.troubled.label} is holding the batch
                  </Link>
                </>
              ) : null}
            </p>
            <div className="flex flex-wrap gap-2">
              <DrawStoriesButton
                featureId={featureId}
                candidates={
                  childIssues.filter(
                    (c) => c.wireframe === "none" && !DONE_STATUSES.has(c.status)
                  ).length
                }
                alreadyDrawn={
                  childIssues.filter(
                    (c) => c.wireframe === "drawn" || c.wireframe === "no-ui"
                  ).length
                }
              />
              <ApproveAllPlansButton
                featureId={featureId}
                pending={rollup.inPlanReview}
              />
            </div>
          </div>
        )}
        {childIssues.length === 0 ? (
          featureStatus === "ready" ? (
            <BreakdownPanel
              featureId={featureId}
              orgId={orgId}
              breakdownMode={breakdownMode}
              breakdownInstructions={breakdownInstructions}
              pending={breakdownPending}
            />
          ) : (
            <p className="text-sm text-muted-foreground">
              This feature needs an approved PRD before it can be broken into
              stories.
            </p>
          )
        ) : (
          <ul className="grid gap-1.5">
            {childIssues.map((c, i) => (
              <li
                key={c.id}
                className="flex items-center gap-3 rounded-md border px-3 py-2 text-sm transition-colors hover:border-ring/60"
              >
                {/* The story id, not the row's position — they agree today and
                    would stop agreeing the moment a story is abandoned. The
                    position is the fallback for a story that predates the
                    numbering (us-7.10) and has no id to show. */}
                <span
                  className="w-20 shrink-0 font-mono text-xs text-muted-foreground"
                  title={c.displayId ? undefined : "No story id — created before work items were numbered"}
                >
                  {c.displayId ?? `#${i + 1}`}
                </span>
                <Link
                  href={`/issues/${c.id}?${backTo}`}
                  className="min-w-0 flex-1 truncate font-medium hover:underline"
                >
                  {c.title}
                </Link>
                {/* US-7.1: advisory, and only ever present once scored — a
                    placeholder chip on every unscored story would read as a
                    real estimate of "unknown". */}
                {/* US-48.3: what the feature's screens look like as a set is
                    the reason to draw a feature at all, so each row says
                    whether it has been drawn without a visit. */}
                <WireframeMark state={c.wireframe} />
                <ComplexityBadge complexity={c.complexity} />
                <StatusBadge status={c.status as IssueStatus} />
                <StoryDispatchMenu
                  issueId={c.id}
                  state={{
                    orgId,
                    status: c.status,
                    hasApprovedPlan: c.hasApprovedPlan,
                    label: c.displayId ?? c.title,
                    buildMode,
                    featureLabel,
                    blockingIssue:
                      blockingIssue && blockingIssue.id !== c.id
                        ? blockingIssue
                        : null,
                  }}
                />
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
