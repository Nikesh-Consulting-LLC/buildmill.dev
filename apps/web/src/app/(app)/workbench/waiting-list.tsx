"use client";

import { useMemo, useState, type ReactNode } from "react";
import Link from "next/link";
import { useRouter } from "@/lib/router-with-progress";
import {
  ArrowRight,
  Check,
  ChevronRight,
  ClipboardCheck,
  FileText,
  Hourglass,
  Loader2,
  RotateCcw,
  Rocket,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { EmptyState } from "@/components/empty-state";
import { TypeBadge, type IssueType } from "@/components/type-badge";
import { apiFetch } from "@/lib/api";
import { cn } from "@/lib/utils";
import { heldReason } from "@/lib/dispatch-block";
import { toastError, toastSuccess } from "@/components/ui/toast";
import { FeatureBatchAction } from "@/components/feature-batch-action";
import { ReviewPeek } from "./review-peek";
import {
  GuidelineRecommendationsGroup,
  type GuidelineRecommendation,
} from "./guideline-recommendations-group";
import {
  GuidelineRefreshGroup,
  type GuidelineRefresh,
} from "./guideline-refresh-group";
import {
  useCollapsedProjects,
  WAITING_COLLAPSE_KEY,
} from "./project-groups";
import type { TodoGroup, TodoItem } from "./data";

// US-6.4 / mockup: the wait age reads as a compact pill — neutral by default,
// amber past a day, red past three.
const AGE_PILL: Record<string, string> = {
  normal: "bg-muted text-muted-foreground",
  warn: "bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-300",
  bad: "bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-300",
};

const DISPATCH_GROUP = "Dispatch";

/** US-24.1: interleave a feature header before the runs of stories that share
 * one, the way Work items nests. Presentation only — every item still appears
 * exactly once, so counts and batch selection are untouched.
 *
 * A feature owning a single row in this group renders flat and names the
 * feature on the row instead: a header over one child is chrome, not
 * information. */
type NestedRow =
  | { kind: "feature"; feature: NonNullable<TodoItem["parent"]> }
  | { kind: "item"; item: TodoItem; nested: boolean };

export function nestByFeature(items: TodoItem[]): NestedRow[] {
  const counts = new Map<string, number>();
  for (const i of items) {
    if (i.parent) counts.set(i.parent.id, (counts.get(i.parent.id) ?? 0) + 1);
  }
  // A feature that will get a synthetic header must not ALSO render its own
  // flat row — the same FEAT-x.y twice in one group (seen live 2026-08-13,
  // FEAT-2.8: its own "no action" row directly above its header). The header
  // already links to the feature and carries the batch action; it IS the
  // feature's row.
  const headerIds = new Set(
    [...counts].filter(([, n]) => n > 1).map(([id]) => id)
  );
  const seen = new Set<string>();
  const out: NestedRow[] = [];
  for (const i of items) {
    if (headerIds.has(i.id)) continue;
    const p = i.parent ?? null;
    const grouped = !!p && (counts.get(p.id) ?? 0) > 1;
    if (p && grouped && !seen.has(p.id)) {
      seen.add(p.id);
      out.push({ kind: "feature", feature: p });
    }
    out.push({ kind: "item", item: i, nested: grouped });
  }
  return out;
}

async function dispatchOne(id: string) {
  await apiFetch(`/api/v1/issues/${id}/dispatch`, { method: "POST" });
}

/** US-68.4: the icon+label a primary action button shows, shared between the
 * desktop table's action cell and the mobile card's full-width button so the
 * two surfaces can never drift into saying different things for the same
 * item. */
function ActionGlyph({
  item,
  isBusy,
}: {
  item: TodoItem;
  isBusy: boolean;
}) {
  if (isBusy) return <Loader2 className="size-3.5 animate-spin" />;
  // US-74.5: an hourglass says "not yet", where a rocket said "go" and then
  // errored.
  if (heldReason(item)) return <Hourglass className="size-3.5" />;
  if (item.mode === "navigate")
    return item.emphasis === "success" ? (
      <FileText className="size-3.5" />
    ) : (
      <ArrowRight className="size-3.5" />
    );
  if (item.mode === "redispatch") return <RotateCcw className="size-3.5" />;
  if (item.mode === "approve") return <Check className="size-3.5" />;
  return <Rocket className="size-3.5" />;
}

// US-6.2: the "Waiting on you" list. Reviews, QA sign-offs, and triage keep
// their full-surface links; the Dispatch and Fix & retry groups act inline
// against the same dispatch endpoint the work-item page uses. The Dispatch
// group can also be dispatched in a batch.
export function WaitingList({
  groups,
  recommendations,
  refreshes,
  orgId,
}: {
  groups: TodoGroup[];
  recommendations: GuidelineRecommendation[];
  refreshes: GuidelineRefresh[];
  /** US-84.1: the feature header's batch confirm previews instructions
   * (us-49.1), and that audit trail needs the org. */
  orgId: string;
}) {
  const router = useRouter();
  const [busy, setBusy] = useState<Record<string, boolean>>({});
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [batchBusy, setBatchBusy] = useState(false);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  function toggleExpand(id: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  // US-74.5: a held item is not selectable. Including it would make "Dispatch
  // selected (3)" mean "dispatch 2 and fail 1", and the count itself would be
  // a small lie.
  const selectableIds = useMemo(() => {
    const g = groups.find((x) => x.title === DISPATCH_GROUP);
    return new Set(
      (g?.items ?? []).filter((i) => !heldReason(i)).map((i) => i.id)
    );
  }, [groups]);

  const selectedCount = useMemo(
    () => [...selected].filter((id) => selectableIds.has(id)).length,
    [selected, selectableIds]
  );

  // US-64.x: approve inline, the same endpoint(s) the full review page's
  // Approve button calls — `item.approve` only exists when the row's gate
  // needs no input this table can't supply (see data.ts). A `thenDispatch`
  // failure is reported as a continuation failure, not an approval failure:
  // the approval already landed and re-clicking would try to approve again.
  async function approveOne(item: TodoItem) {
    if (!item.approve) return;
    setBusy((b) => ({ ...b, [item.id]: true }));
    let alreadyMerged = false;
    try {
      const result = await apiFetch(item.approve.endpoint, { method: "POST" });
      // US-79.2: the PR was merged by hand on GitHub and approve reconciled —
      // a success, but one worth saying differently.
      alreadyMerged = result?.merge === "already-merged";
      // US-72.2: a real merge conflict answers 200 with ok:false — a
      // decision, not an error (US-40.1). Nothing was approved; toasting
      // "Approved" here is how a stuck-looking pipeline was born. The
      // review page owns the conflict flow, so go there.
      if (result?.merge_conflict) {
        toastError(
          "Merge conflict — nothing approved",
          "The PR can't merge as-is. Approve from the review page to see the conflict and send it back."
        );
        setBusy((b) => ({ ...b, [item.id]: false }));
        router.push(item.href);
        return;
      }
    } catch (e) {
      toastError("Couldn't approve", (e as Error).message);
      setBusy((b) => ({ ...b, [item.id]: false }));
      return;
    }
    if (item.approve.thenDispatch) {
      try {
        await apiFetch(item.approve.thenDispatch, { method: "POST" });
      } catch (e) {
        toastError(
          "Approved, but the next run could not be dispatched",
          (e as Error).message
        );
        router.refresh();
        return;
      }
    }
    if (alreadyMerged) {
      toastSuccess("Already merged on GitHub", item.title);
    } else {
      toastSuccess("Approved", item.title);
    }
    router.refresh();
  }

  async function runInline(item: TodoItem) {
    if (item.mode === "approve") return approveOne(item);
    if (heldReason(item)) return;
    setBusy((b) => ({ ...b, [item.id]: true }));
    try {
      await dispatchOne(item.id);
      toastSuccess(
        item.mode === "redispatch" ? "Re-dispatched" : "Dispatched",
        item.title
      );
      // Leave the row disabled; it drops out of the list on the refresh.
      router.refresh();
    } catch (e) {
      toastError("Couldn't dispatch", (e as Error).message);
      setBusy((b) => ({ ...b, [item.id]: false }));
    }
  }

  // US-68.4: the mobile card list has no per-item checkboxes (fiddly to hit
  // precisely on a phone) — its "Dispatch all" button passes every id in the
  // group explicitly instead of reading `selected`.
  //
  // US-85.2: ONE request; the server sorts by build order and reports every
  // item. The old client-side loop dispatched in checkbox-CLICK order and a
  // closed tab truncated the batch — that is how story 9 planned before
  // stories 4–8 on 2026-08-12 while five siblings never dispatched at all.
  async function runBatch(explicitIds?: string[]) {
    const ids = explicitIds ?? [...selected].filter((id) => selectableIds.has(id));
    if (!ids.length) return;
    setBatchBusy(true);
    try {
      const result = await apiFetch(`/api/v1/issues/batch-dispatch`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ issue_ids: ids }),
      });
      const ok: number = result?.dispatched?.length ?? 0;
      const skipped: { id: string; reason: string }[] = result?.skipped ?? [];
      if (skipped.length === 0) {
        toastSuccess(
          `Dispatched ${ok}`,
          `${ok} work item(s) sent to the factory, in build order`
        );
      } else {
        toastError(
          `${ok} dispatched, ${skipped.length} skipped`,
          skipped[0]?.reason
            ? `First skip: ${skipped[0].reason} — skipped items are still listed.`
            : "The skipped items are still listed — try them individually."
        );
      }
    } catch (e) {
      toastError("Couldn't dispatch the batch", (e as Error).message);
    }
    setBatchBusy(false);
    setSelected(new Set());
    router.refresh();
  }

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  // US-91.4: project is the outer level of this tab. The existing groups
  // (Dispatch, Review, QA sign-off, …) keep their order inside each project,
  // so folding a project hides everything for it in one click instead of
  // five. Counts are unaffected: collapsing is a view state.
  const sections = useMemo(() => {
    const order = groups.map((g) => g.title);
    const byProject = new Map<
      string,
      { id: string; name: string; items: Map<string, TodoItem[]> }
    >();
    for (const g of groups) {
      for (const item of g.items) {
        const entry = byProject.get(item.projectId) ?? {
          id: item.projectId,
          name: item.project,
          items: new Map<string, TodoItem[]>(),
        };
        const bucket = entry.items.get(g.title) ?? [];
        bucket.push(item);
        entry.items.set(g.title, bucket);
        byProject.set(item.projectId, entry);
      }
    }
    return [...byProject.values()]
      .map((entry) => ({
        id: entry.id,
        name: entry.name,
        count: [...entry.items.values()].reduce((n, a) => n + a.length, 0),
        groups: order
          .filter((title) => entry.items.has(title))
          .map((title) => ({ title, items: entry.items.get(title)! })),
      }))
      .sort((a, b) => a.name.localeCompare(b.name));
  }, [groups]);
  // AC5: one project renders flat — no grouping chrome at all.
  const grouped = sections.length > 1;
  const { collapsed, toggle: toggleProject } = useCollapsedProjects(
    WAITING_COLLAPSE_KEY
  );

  function renderGroupCards(g: TodoGroup) {

          const isDispatch = g.title === DISPATCH_GROUP;
          const dispatchableIds = g.items
            .filter(
              (i) =>
                (i.mode === "dispatch" || i.mode === "redispatch") &&
                !heldReason(i)
            )
            .map((i) => i.id);
          return (
            <div key={g.title} className="grid gap-2">
              <div className="flex items-center justify-between gap-2 px-1">
                <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  {g.title} ({g.items.length})
                </span>
                {isDispatch && dispatchableIds.length > 1 && (
                  <Button
                    size="sm"
                    className="h-8"
                    onClick={() => runBatch(dispatchableIds)}
                    disabled={batchBusy}
                  >
                    {batchBusy ? (
                      <Loader2 className="size-3.5 animate-spin" />
                    ) : (
                      <Rocket className="size-3.5" />
                    )}
                    Dispatch all ({dispatchableIds.length})
                  </Button>
                )}
              </div>
              {nestByFeature(g.items).map((row) => {
                if (row.kind === "feature") {
                  return (
                    <div
                      key={`${g.title}-feat-${row.feature.id}`}
                      className="flex min-w-0 items-center justify-between gap-2 px-1 text-xs text-muted-foreground"
                    >
                      <span className="flex min-w-0 items-center gap-2">
                        <TypeBadge type={"feature" as IssueType} />
                        {row.feature.displayId && (
                          <span className="font-mono">{row.feature.displayId}</span>
                        )}
                        <Link
                          href={`/issues/${row.feature.id}?from=workbench`}
                          className="truncate font-medium text-foreground hover:underline"
                        >
                          {row.feature.title}
                        </Link>
                      </span>
                      {!!row.feature.batchGate && (
                        <FeatureBatchAction
                          featureId={row.feature.id}
                          orgId={orgId}
                          gate={row.feature.batchGate}
                          compact
                        />
                      )}
                    </div>
                  );
                }
                const item = row.item;
                const rowBusy = busy[item.id] || batchBusy;
                const isOpen = expanded.has(item.id);
                const held = heldReason(item);
                return (
                  <div
                    key={`${g.title}-${item.id}-card`}
                    className={cn(
                      "flex min-w-0 flex-col gap-2 rounded-lg border p-3",
                      row.nested && "ml-4"
                    )}
                  >
                    <div className="flex min-w-0 items-start justify-between gap-2">
                      <span className="flex min-w-0 flex-col gap-0.5">
                        <span className="flex min-w-0 items-center gap-2">
                          <TypeBadge type={item.type as IssueType} />
                          {item.displayId && (
                            <span className="shrink-0 font-mono text-xs text-muted-foreground">
                              {item.displayId}
                            </span>
                          )}
                        </span>
                        <Link
                          href={`/issues/${item.id}?from=workbench`}
                          className="font-medium hover:underline"
                        >
                          {item.title}
                        </Link>
                        <span className="truncate text-xs text-muted-foreground">
                          {item.project} · {item.reason}
                        </span>
                        {held && (
                          <span className="flex items-start gap-1.5 text-xs text-amber-700 dark:text-amber-300">
                            <Hourglass className="mt-0.5 size-3 shrink-0" />
                            <span className="min-w-0">{held}</span>
                          </span>
                        )}
                      </span>
                      <span
                        className={cn(
                          "shrink-0 rounded-full px-2 py-0.5 font-mono text-[10.5px] font-semibold tabular-nums",
                          AGE_PILL[item.ageLevel]
                        )}
                        title={`Waiting ${item.age} in this state`}
                      >
                        {item.age}
                      </span>
                    </div>
                    {(item.peekKind || item.inspectHref) && (
                      <div className="flex items-center gap-2">
                        {item.peekKind && (
                          <Button
                            variant="ghost"
                            size="sm"
                            className="h-8 flex-1"
                            onClick={() => toggleExpand(item.id)}
                            aria-expanded={isOpen}
                          >
                            <ChevronRight
                              className={cn(
                                "size-3.5 transition-transform",
                                isOpen && "rotate-90"
                              )}
                            />
                            Review
                          </Button>
                        )}
                        {item.inspectHref && (
                          <Button
                            variant="ghost"
                            size="sm"
                            className="h-8 flex-1"
                            render={<Link href={item.inspectHref} />}
                          >
                            Inspect
                          </Button>
                        )}
                      </div>
                    )}
                    {item.peekKind && isOpen && (
                      <ReviewPeek
                        issueId={item.id}
                        peekKind={item.peekKind}
                        workItem={{ title: item.title, displayId: item.displayId }}
                      />
                    )}
                    {item.mode === "navigate" ? (
                      <Button
                        variant={item.emphasis === "success" ? "success" : "outline"}
                        className="h-11 w-full"
                        render={<Link href={item.href} />}
                      >
                        <ActionGlyph item={item} isBusy={false} />
                        {item.action}
                      </Button>
                    ) : (
                      <Button
                        variant={item.mode === "approve" ? "success" : "default"}
                        className="h-11 w-full"
                        onClick={() => runInline(item)}
                        disabled={rowBusy || !!held}
                        title={held ?? undefined}
                        aria-label={held ? `Waiting — ${held}` : undefined}
                      >
                        <ActionGlyph item={item} isBusy={!!busy[item.id]} />
                        {held ? "Waiting" : item.action}
                      </Button>
                    )}
                  </div>
                );
              })}
            </div>
          );
  }

  function renderGroupRows(g: TodoGroup): ReactNode[] {

              const isDispatch = g.title === DISPATCH_GROUP;
              return [
                <TableRow key={g.title} className="hover:bg-transparent">
                  <TableCell colSpan={5} className="bg-muted/50 py-1.5">
                    <span className="flex items-center justify-between gap-2">
                      <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                        {g.title} ({g.items.length})
                      </span>
                      {isDispatch && selectedCount > 0 && (
                        <Button
                          size="sm"
                          onClick={() => runBatch()}
                          disabled={batchBusy}
                          className="h-6"
                        >
                          {batchBusy ? (
                            <Loader2 className="size-3.5 animate-spin" />
                          ) : (
                            <Rocket className="size-3.5" />
                          )}
                          Dispatch selected ({selectedCount})
                        </Button>
                      )}
                    </span>
                  </TableCell>
                </TableRow>,
                ...nestByFeature(g.items).flatMap((row) => {
                  if (row.kind === "feature") {
                    return [
                      <TableRow
                        key={`${g.title}-feat-${row.feature.id}`}
                        className="hover:bg-transparent"
                      >
                        <TableCell />
                        <TableCell colSpan={4} className="py-1">
                          <span className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
                            <span className="flex min-w-0 items-center gap-2">
                              <TypeBadge type={"feature" as IssueType} />
                              {row.feature.displayId && (
                                <span className="font-mono">
                                  {row.feature.displayId}
                                </span>
                              )}
                              <Link
                                href={`/issues/${row.feature.id}?from=workbench`}
                                className="truncate font-medium text-foreground hover:underline"
                              >
                                {row.feature.title}
                              </Link>
                            </span>
                            {/* US-25.2 → US-84.1: the one exception to
                                us-24.1's "the feature header carries no
                                action". That rule stops a second dispatch
                                competing with the story rows; this does
                                something no row can — clear the whole gate —
                                and only appears when every sibling is at the
                                same one. */}
                            {!!row.feature.batchGate && (
                              <FeatureBatchAction
                                featureId={row.feature.id}
                                orgId={orgId}
                                gate={row.feature.batchGate}
                                compact
                              />
                            )}
                          </span>
                        </TableCell>
                      </TableRow>,
                    ];
                  }
                  const item = row.item;
                  const rowBusy = busy[item.id] || batchBusy;
                  const isOpen = expanded.has(item.id);
                  const held = heldReason(item);
                  return [
                    <TableRow key={`${g.title}-${item.id}`}>
                      <TableCell>
                        {isDispatch && !held && (
                          <Checkbox
                            checked={selected.has(item.id)}
                            onCheckedChange={() => toggle(item.id)}
                            disabled={batchBusy}
                            aria-label={`Select ${item.title}`}
                          />
                        )}
                      </TableCell>
                      <TableCell
                        className={cn(
                          "w-full max-w-0 min-w-0",
                          row.nested && "pl-6"
                        )}
                      >
                        <span className="flex min-w-0 flex-col">
                          <span className="flex min-w-0 items-center gap-2">
                            <TypeBadge type={item.type as IssueType} />
                            {item.displayId && (
                              <span className="shrink-0 font-mono text-xs text-muted-foreground">
                                {item.displayId}
                              </span>
                            )}
                            <Link
                              href={`/issues/${item.id}?from=workbench`}
                              className="min-w-0 truncate font-medium hover:underline"
                            >
                              {item.title}
                            </Link>
                            <span className="shrink-0 text-xs text-muted-foreground">
                              {item.project}
                            </span>
                          </span>
                          {/* US-35.7: "Why it's here" and Age are columns only
                              at `lg`. Below it, both ride here — the reason is
                              why the row exists and must not simply vanish. */}
                          <span className="truncate text-xs text-muted-foreground lg:hidden">
                            {item.reason} · waiting {item.age}
                          </span>
                          {/* US-74.5: the reason rides the title cell, not the
                              narrow "Why it's here" column — it is a sentence
                              naming another work item and truncating it to
                              "waiting on an ear…" would defeat the point. */}
                          {held && (
                            <span className="flex items-start gap-1.5 text-xs text-amber-700 dark:text-amber-300">
                              <Hourglass className="mt-0.5 size-3 shrink-0" />
                              <span className="min-w-0">{held}</span>
                            </span>
                          )}
                        </span>
                      </TableCell>
                      <TableCell className="hidden truncate text-xs text-muted-foreground lg:table-cell">
                        {item.reason}
                      </TableCell>
                      <TableCell className="hidden lg:table-cell">
                        <span
                          className={cn(
                            "rounded-full px-2 py-0.5 font-mono text-[10.5px] font-semibold tabular-nums",
                            AGE_PILL[item.ageLevel]
                          )}
                          title={`Waiting ${item.age} in this state`}
                        >
                          {item.age}
                        </span>
                      </TableCell>
                      <TableCell>
                        <span className="flex items-center justify-end gap-1.5">
                          {item.peekKind && (
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => toggleExpand(item.id)}
                              aria-expanded={isOpen}
                            >
                              <ChevronRight
                                className={cn(
                                  "size-3.5 transition-transform",
                                  isOpen && "rotate-90"
                                )}
                              />
                              Peek
                            </Button>
                          )}
                          {item.inspectHref && (
                            <Button
                              variant="ghost"
                              size="sm"
                              render={<Link href={item.inspectHref} />}
                            >
                              Inspect
                            </Button>
                          )}
                          {item.mode === "navigate" ? (
                            <Button
                              variant={
                                item.emphasis === "success"
                                  ? "success"
                                  : "outline"
                              }
                              size="sm"
                              render={<Link href={item.href} />}
                            >
                              <ActionGlyph item={item} isBusy={false} />
                              {item.action}
                            </Button>
                          ) : (
                            <Button
                              variant={
                                item.mode === "approve" ? "success" : "outline"
                              }
                              size="sm"
                              onClick={() => runInline(item)}
                              disabled={rowBusy || !!held}
                              title={held ?? undefined}
                              aria-label={held ? `Waiting — ${held}` : undefined}
                            >
                              <ActionGlyph item={item} isBusy={!!busy[item.id]} />
                              {held ? "Waiting" : item.action}
                            </Button>
                          )}
                        </span>
                      </TableCell>
                    </TableRow>,
                    item.peekKind && isOpen ? (
                      <TableRow
                        key={`${g.title}-${item.id}-peek`}
                        className="hover:bg-transparent"
                      >
                        <TableCell />
                        <TableCell colSpan={4} className="pt-0">
                          <ReviewPeek
                            issueId={item.id}
                            peekKind={item.peekKind}
                            workItem={{
                              title: item.title,
                              displayId: item.displayId,
                            }}
                          />
                        </TableCell>
                      </TableRow>
                    ) : null,
                  ];
                }),
              ];
  }

  if (
    groups.length === 0 &&
    recommendations.length === 0 &&
    refreshes.length === 0
  ) {
    return (
      <EmptyState
        icon={ClipboardCheck}
        title="Inbox zero"
        description="Nothing needs a decision."
      />
    );
  }

  return (
    <div className="grid min-w-0 gap-4">
      {/* US-68.4: below `md` a table has no room for a title, a reason, and
          an action cell at once — a card per item, one full-width primary
          action, replaces it outright rather than trying to squeeze columns
          further. Same groups, same nesting, same handlers as the table. */}
      <div className="grid gap-4 md:hidden">
        {sections.flatMap((section) => {
          const folded = grouped && collapsed.has(section.id);
          const head = grouped
            ? [
                <button
                  key={`proj-${section.id}`}
                  type="button"
                  onClick={() => toggleProject(section.id)}
                  aria-expanded={!folded}
                  className="flex w-full items-center gap-1.5 rounded-md bg-muted/50 px-2 py-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground"
                >
                  <ChevronRight
                    className={cn(
                      "size-3.5 transition-transform",
                      !folded && "rotate-90"
                    )}
                  />
                  {section.name} ({section.count})
                </button>,
              ]
            : [];
          if (folded) return head;
          return [...head, ...section.groups.map(renderGroupCards)];
        })}
      </div>
      {/* US-19.1: one compact table, group headings as spanning rows, so the
          reason and the age line up in columns instead of wrapping under each
          title. Dispatch/redispatch/peek behavior is unchanged. */}
      <div className="hidden min-w-0 rounded-lg border md:block">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-8" />
              <TableHead>Work item</TableHead>
              <TableHead className="hidden w-44 lg:table-cell">Why it&apos;s here</TableHead>
              <TableHead className="hidden w-20 lg:table-cell">Age</TableHead>
              <TableHead className="w-56" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {sections.flatMap((section) => {
              const folded = grouped && collapsed.has(section.id);
              const head = grouped
                ? [
                    <TableRow
                      key={`proj-${section.id}`}
                      className="hover:bg-transparent"
                    >
                      <TableCell colSpan={5} className="bg-muted py-1.5">
                        <button
                          type="button"
                          onClick={() => toggleProject(section.id)}
                          aria-expanded={!folded}
                          className="flex w-full items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground hover:text-foreground"
                        >
                          <ChevronRight
                            className={cn(
                              "size-3.5 transition-transform",
                              !folded && "rotate-90"
                            )}
                          />
                          {section.name} ({section.count})
                        </button>
                      </TableCell>
                    </TableRow>,
                  ]
                : [];
              if (folded) return head;
              return [...head, ...section.groups.flatMap(renderGroupRows)];
            })}
          </TableBody>
        </Table>
      </div>
      <GuidelineRefreshGroup items={refreshes} />
      <GuidelineRecommendationsGroup items={recommendations} />
    </div>
  );
}
