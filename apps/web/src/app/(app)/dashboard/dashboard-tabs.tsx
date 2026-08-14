"use client";

// US-19.1: Things to Do is one section with four tabs, each a compact table at
// full page width. The old two-column card grid squeezed the right-hand cards
// to ~380px, which truncated exactly the thing a manager wants mid-run: which
// agent has the work, and how long it has had it.

import { useCallback, useState } from "react";
import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { useRouter } from "@/lib/router-with-progress";
import { Bot, CheckCircle2, ChevronRight, ClipboardCheck, Rocket } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { StatusBadge, type IssueStatus } from "@/components/status-badge";
import { TypeBadge, type IssueType } from "@/components/type-badge";
import { AgentText } from "@/components/agent-text";
import { EmptyState } from "@/components/empty-state";
import { cn } from "@/lib/utils";
import { money } from "@/lib/budget";
import { RequeueButton } from "./requeue-button";
import { InProgressSection } from "./in-progress-section";
import { ReleaseSuggestions } from "./release-suggestions";
import { WaitingList } from "./waiting-list";
import { formatDuration, formatMinutes } from "./duration";
import type { GuidelineRecommendation } from "./guideline-recommendations-group";
import type { GuidelineRefresh } from "./guideline-refresh-group";
import type {
  AgentItem,
  CompletedItem,
  DeployRow,
  FeatureRunInfo,
  ReleaseRow,
  ReleaseSuggestion,
  TodoGroup,
} from "./data";

const TABS = ["waiting", "factory", "completed", "releases"] as const;

/** US-24.2: In the factory nests the same way Waiting on you does — a feature
 * header above the stories that share it, so the queue's hold reasons ("story
 * US-1.1.1 ahead of this one") point at a row you can actually see.
 *
 * Presentation only: every item still appears exactly once, so the tab count
 * is unchanged. A feature owning one row renders flat. */
type ParentRef = NonNullable<AgentItem["parent"]>;
type NestedAgentRow =
  | { kind: "feature"; feature: ParentRef }
  | { kind: "item"; item: AgentItem; nested: boolean };

function nestAgentItems(items: AgentItem[]): NestedAgentRow[] {
  const counts = new Map<string, number>();
  for (const i of items) {
    if (i.parent) counts.set(i.parent.id, (counts.get(i.parent.id) ?? 0) + 1);
  }
  const seen = new Set<string>();
  const out: NestedAgentRow[] = [];
  for (const i of items) {
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
type TabKey = (typeof TABS)[number];

function isTabKey(v: string | null): v is TabKey {
  return v != null && (TABS as readonly string[]).includes(v);
}

/** Muted numeric cell — durations and ages line up in a column. */
const NUM = "font-mono text-xs tabular-nums text-muted-foreground";

export function DashboardTabs({
  groups,
  recommendations,
  refreshes,
  agentItems,
  featureRuns,
  interactiveByPrincipal,
  releaseSuggestions,
  completedItems,
  releaseRows,
  deployRows,
  waitingCount,
  orgId,
}: {
  groups: TodoGroup[];
  recommendations: GuidelineRecommendation[];
  refreshes: GuidelineRefresh[];
  agentItems: AgentItem[];
  /** US-86.2: live feature-owned runs, keyed by feature issue id. */
  featureRuns: Record<string, FeatureRunInfo>;
  /** US-91.3: which claiming agents run the `interactive` module. */
  interactiveByPrincipal: Record<string, boolean>;
  /** US-91.18: projects holding merged work no release has shipped. */
  releaseSuggestions: ReleaseSuggestion[];
  completedItems: CompletedItem[];
  releaseRows: ReleaseRow[];
  deployRows: DeployRow[];
  waitingCount: number;
  /** US-84.1: passed through to WaitingList for the feature-header batch. */
  orgId: string;
}) {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();

  // US-92.1: one source for both the desktop bar and the phone select.
  const TAB_ITEMS: { key: TabKey; label: string }[] = [
    { key: "waiting", label: "Dispatch" },
    { key: "factory", label: "In the factory" },
    { key: "completed", label: "Completed" },
    { key: "releases", label: "Releases" },
  ];
  const counts: Record<TabKey, number> = {
    waiting: waitingCount,
    factory: agentItems.length,
    completed: completedItems.length,
    releases: releaseRows.length + deployRows.length,
  };

  const urlTab = params.get("tab");
  const active: TabKey = isTabKey(urlTab) ? urlTab : "waiting";

  // The tab rides the URL beside the existing ?project= param, so a reload or a
  // shared link lands on the same facet.
  const select = useCallback(
    (value: string) => {
      const next = new URLSearchParams(params.toString());
      if (value === "waiting") next.delete("tab");
      else next.set("tab", value);
      const qs = next.toString();
      router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
    },
    [params, pathname, router]
  );

  return (
    <Tabs value={active} onValueChange={select}>
      {/* The list is w-fit by default, so on a narrow window the last tabs are
          clipped and unreachable. Let the bar itself scroll instead. */}
      {/* US-92.1: measured at 375px the four triggers total 555px in a 375px
          bar, so two of them sat off-screen behind a scroll gesture with no
          scrollbar and no affordance — hidden, in practice. Below `md` the
          same four become a control that fits, counts intact, because the
          counts are why the manager looks. */}
      <div className="md:hidden">
        <Select
          items={TAB_ITEMS.map((t) => ({
            value: t.key,
            label: `${t.label} · ${counts[t.key]}`,
          }))}
          value={active}
          onValueChange={(v) => {
            if (typeof v === "string") select(v);
          }}
        >
          <SelectTrigger className="w-full">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {TAB_ITEMS.map((t) => (
              <SelectItem key={t.key} value={t.key}>
                {t.label} · {counts[t.key]}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <TabsList className="hidden h-9 max-w-full overflow-x-auto md:inline-flex">
        {/* US-91.1: named for the act, not the state. Every row on this tab
            ends in one of two clicks — dispatch it, or approve it and let it
            dispatch — so "Dispatch" says what the tab is for, and lets
            us-91.2's In Progress section sit on it without contradiction.
            The URL key stays `waiting`: renaming it would break saved links
            to buy nothing. */}
        <TabsTrigger value="waiting" className="h-7">
          <ClipboardCheck className="size-3.5" />
          Dispatch
          <TabCount n={waitingCount} />
        </TabsTrigger>
        <TabsTrigger value="factory" className="h-7">
          <Bot className="size-3.5" />
          In the factory
          <TabCount n={agentItems.length} />
        </TabsTrigger>
        <TabsTrigger value="completed" className="h-7">
          <CheckCircle2 className="size-3.5" />
          Completed
          <TabCount n={completedItems.length} />
        </TabsTrigger>
        <TabsTrigger value="releases" className="h-7">
          <Rocket className="size-3.5" />
          Releases
          <TabCount n={releaseRows.length + deployRows.length} />
        </TabsTrigger>
      </TabsList>

      <TabsContent value="waiting" className="grid gap-5">
        {/* US-91.2: what is already being worked on, above the decision about
            sending more in. */}
        <InProgressSection
          items={agentItems}
          featureRuns={featureRuns}
          interactiveByPrincipal={interactiveByPrincipal}
        />
        {/* US-91.18: work that is built and waiting on a cut. */}
        <ReleaseSuggestions suggestions={releaseSuggestions} />
        <WaitingList
          groups={groups}
          recommendations={recommendations}
          refreshes={refreshes}
          orgId={orgId}
        />
      </TabsContent>

      <TabsContent value="factory">
        <FactoryTable items={agentItems} featureRuns={featureRuns} />
      </TabsContent>

      <TabsContent value="completed">
        <CompletedTable items={completedItems} />
      </TabsContent>

      <TabsContent value="releases">
        <ReleasesTable releases={releaseRows} deploys={deployRows} />
      </TabsContent>
    </Tabs>
  );
}

function TabCount({ n }: { n: number }) {
  return (
    <span className="rounded-full bg-background/70 px-1.5 font-mono text-[10.5px] tabular-nums">
      {n}
    </span>
  );
}

/** Type icon + display id + title — the identity block every table shares. */
function ItemCell({
  href,
  type,
  displayId,
  title,
  subline,
}: {
  href: string;
  type: string;
  displayId: string | null;
  title: string;
  /** US-35.6: what the columns hidden below `lg` said. Shown only at those
   *  widths, so the facts survive where the column cannot fit. */
  subline?: string | null;
}) {
  return (
    <span className="flex min-w-0 flex-col">
      <span className="flex min-w-0 items-center gap-2">
        {type && <TypeBadge type={type as IssueType} />}
        {displayId && (
          <span className="shrink-0 font-mono text-xs text-muted-foreground">
            {displayId}
          </span>
        )}
        <Link href={href} className="min-w-0 truncate hover:underline">
          {title}
        </Link>
      </span>
      {subline && (
        <span className="truncate text-xs text-muted-foreground lg:hidden">
          {subline}
        </span>
      )}
    </span>
  );
}

// US-19.1: agent work only — every row has an agent and an elapsed time.
// Deploys moved to Releases so this table has no exceptions.
/**
 * US-35.5: the Agent column, which read "—" for anything not currently running.
 *
 * A running row names the agent and links to its profile. A queued row says who
 * *could* take it — never who will, because the factory does not pre-assign:
 * a queued run sits in a pool and any eligible agent claims it first-come. A
 * row nobody can take says so, with the condition that failed, which is the
 * case worth spotting before the item quietly ages out.
 */
function FactoryAgentCell({ item }: { item: AgentItem }) {
  if (item.workerName) {
    // US-39.4: to the agent's CONSOLE, not the roster drawer. On this tab the
    // question behind the name is "what is it doing" — and since us-39.3 the
    // console leads with exactly that: the item, the live activity, and how
    // long it has been at it. The roster answers "who is this", which is not
    // what anyone reading a factory row is asking.
    return item.workerPrincipalId ? (
      <Link
        href={`/team/${item.workerPrincipalId}/runner`}
        className="truncate hover:underline"
        title={`What ${item.workerName} is doing right now`}
      >
        {item.workerName}
      </Link>
    ) : (
      <span className="truncate">{item.workerName}</span>
    );
  }

  const eligible = item.eligible;
  if (!eligible) return <span className="text-muted-foreground">—</span>;

  if (eligible.blockedReason) {
    return (
      <span
        className="truncate text-amber-700 dark:text-amber-400"
        title={eligible.blockedReason}
      >
        Nobody can take this
      </span>
    );
  }

  // Few enough to name, or a count that expands on hover — the manager wants
  // "who", and a bare number answers a different question.
  const names = eligible.agents.map((a) => a.name);
  if (names.length <= 2) {
    return (
      <span className="truncate text-muted-foreground" title={names.join(", ")}>
        {names.join(", ")}
      </span>
    );
  }
  return (
    <span className="truncate text-muted-foreground" title={names.join(", ")}>
      any of {names.length} agents
    </span>
  );
}

function FactoryTable({
  items,
  featureRuns,
}: {
  items: AgentItem[];
  featureRuns: Record<string, FeatureRunInfo>;
}) {
  const [open, setOpen] = useState<Set<string>>(new Set());

  function toggle(id: string) {
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  if (items.length === 0) {
    return (
      <EmptyState
        icon={Bot}
        title="Nothing running"
        description="Everything is waiting on you or done. Dispatched work shows here with the agent that claimed it."
      />
    );
  }

  return (
    <div className="grid gap-2">
      {/* US-15.2: the queue behind this tab — every claimable and in-progress
          run, in worker-pull order, reorderable and pausable. */}
      <div className="flex justify-end">
        <Button
          size="sm"
          variant="ghost"
          className="h-7 px-2 text-xs"
          render={<Link href="/factory-queue" />}
        >
          Queue details
        </Button>
      </div>
      <div className="min-w-0 rounded-lg border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-8" />
            <TableHead className="w-full max-w-0">Work item</TableHead>
            <TableHead className="w-40">Stage</TableHead>
            <TableHead className="w-36">Agent</TableHead>
            <TableHead className="hidden w-24 lg:table-cell">Elapsed</TableHead>
            <TableHead className="hidden w-28 lg:table-cell">Heard</TableHead>
            <TableHead className="w-24" />
          </TableRow>
        </TableHeader>
        <TableBody>
          {nestAgentItems(items).map((row) => {
            if (row.kind === "feature") {
              const stories = items.filter(
                (x) => x.parent?.id === row.feature.id
              );
              // US-86.2: a feature-owned build is ONE run whose issue is the
              // feature — the header carries its truth (stage, agent,
              // elapsed, heard) instead of eleven story rows faking
              // "Running" with no agent behind them.
              const run = featureRuns[row.feature.id];
              return [
                <TableRow key={`feat-${row.feature.id}`} className="hover:bg-transparent">
                  <TableCell />
                  <TableCell colSpan={6} className="py-1">
                    <span className="flex min-w-0 items-center gap-2 text-xs text-muted-foreground">
                      {row.feature.displayId && (
                        <span className="font-mono">{row.feature.displayId}</span>
                      )}
                      <Link
                        href={`/issues/${row.feature.id}?from=dashboard`}
                        className="truncate font-medium text-foreground hover:underline"
                      >
                        {row.feature.title}
                      </Link>
                      {run ? (
                        <>
                          <Badge variant="secondary">Building</Badge>
                          <span className="truncate">
                            {run.workerPrincipalId ? (
                              <Link
                                href={`/team/${run.workerPrincipalId}`}
                                className="hover:underline"
                              >
                                {run.workerName}
                              </Link>
                            ) : (
                              run.workerName
                            )}{" "}
                            · {formatMinutes(run.runningMinutes)} elapsed ·
                            heard{" "}
                            <span
                              className={cn(
                                run.isSilent &&
                                  "text-amber-700 dark:text-amber-400"
                              )}
                            >
                              {run.silentMinutes === 0
                                ? "just now"
                                : formatMinutes(run.silentMinutes)}
                            </span>
                          </span>
                          <span className="shrink-0">
                            · one run, {stories.length}{" "}
                            {stories.length === 1 ? "story" : "stories"}
                          </span>
                        </>
                      ) : (
                        <span>
                          · {stories.length}{" "}
                          {stories.length === 1 ? "story" : "stories"} in the
                          factory
                        </span>
                      )}
                    </span>
                  </TableCell>
                </TableRow>,
              ];
            }
            const i = row.item;
            const isOpen = open.has(i.id);
            // US-86.2: a story inside a live feature-owned build is cargo —
            // the run's telemetry lives on the header above it, and a
            // per-story "Running" pill over em-dashes was a fabrication.
            const cargo =
              row.nested && !!i.parent && !!featureRuns[i.parent.id];
            return [
              <TableRow key={i.id}>
                <TableCell className="align-middle">
                  {i.lastNote && (
                    <Button
                      variant="ghost"
                      size="sm"
                      className="size-6 p-0"
                      onClick={() => toggle(i.id)}
                      aria-expanded={isOpen}
                      aria-label={`${isOpen ? "Hide" : "Show"} the agent's note on ${i.title}`}
                    >
                      <ChevronRight
                        className={cn(
                          "size-3.5 transition-transform",
                          isOpen && "rotate-90"
                        )}
                      />
                    </Button>
                  )}
                </TableCell>
                <TableCell
                  className={cn(
                    "w-full max-w-0 min-w-0",
                    row.nested && "pl-6"
                  )}
                >
                  <ItemCell
                    href={`/issues/${i.id}?from=dashboard`}
                    type={i.type}
                    displayId={i.displayId}
                    title={i.title}
                    // US-35.6: Elapsed and Heard are columns only at `lg`; at
                    // tablet width they ride here rather than being lost.
                    subline={
                      i.runningMinutes !== undefined
                        ? `${formatMinutes(i.runningMinutes)} elapsed · heard ${
                            i.silentMinutes === 0
                              ? "just now"
                              : formatMinutes(i.silentMinutes)
                          }`
                        : null
                    }
                  />
                </TableCell>
                <TableCell>
                  {/* US-39.4: a held run reads "Waiting", not "Queued" —
                      queued implies it is about to be picked up, and this one
                      cannot be. The reason is the tooltip rather than a second
                      line: six held siblings each repeating the same sentence
                      turned the column into a wall of text, and the sentence is
                      the same for all of them.

                      US-15.6: otherwise a tracked PRD/breakdown run reads
                      queued vs. running distinctly, and anything shown via a
                      real issue status keeps its status pill. */}
                  {cargo ? (
                    <span className="text-xs text-muted-foreground">
                      in this build
                    </span>
                  ) : i.holdReason ? (
                    <Badge
                      variant="outline"
                      className="cursor-help border-amber-300 text-amber-700 dark:border-amber-900 dark:text-amber-400"
                      title={i.holdReason}
                    >
                      Waiting
                    </Badge>
                  ) : i.activeRun ? (
                    i.activeRun.status === "queued" ? (
                      <Badge variant="outline">Queued</Badge>
                    ) : (
                      <Badge variant="secondary">
                        {i.activeRun.kind === "breakdown"
                          ? "Creating stories"
                          : "Drafting PRD"}
                      </Badge>
                    )
                  ) : (
                    <StatusBadge status={i.status as IssueStatus} />
                  )}
                </TableCell>
                <TableCell className="max-w-52 truncate text-xs">
                  {cargo ? null : <FactoryAgentCell item={i} />}
                </TableCell>
                <TableCell className={cn(NUM, "hidden lg:table-cell")}>
                  {cargo ? null : formatMinutes(i.runningMinutes)}
                </TableCell>
                <TableCell
                  className={cn(
                    NUM,
                    "hidden lg:table-cell",
                    i.isSilent && "text-amber-700 dark:text-amber-400"
                  )}
                  title={
                    i.isSilent
                      ? "The worker has not spoken for materially longer than its heartbeat cadence — silence is not the same as work."
                      : undefined
                  }
                >
                  {cargo
                    ? null
                    : i.silentMinutes === 0
                      ? "just now"
                      : formatMinutes(i.silentMinutes)}
                </TableCell>
                <TableCell>
                  {i.isSilent && i.runId && <RequeueButton runId={i.runId} />}
                </TableCell>
              </TableRow>,
              isOpen && i.lastNote ? (
                <TableRow key={`${i.id}-note`} className="hover:bg-transparent">
                  <TableCell />
                  <TableCell colSpan={6} className="pt-0 whitespace-normal">
                    {/* US-14.1: the agent's own words, as the markdown it
                        wrote — no longer clamped to 40 characters. */}
                    <AgentText className="border-l-2 pl-3 text-xs text-muted-foreground">
                      {i.lastNote}
                    </AgentText>
                  </TableCell>
                </TableRow>
              ) : null,
            ];
          })}
        </TableBody>
      </Table>
      </div>
    </div>
  );
}

// US-19.1: one row per finished run — one agent, one duration, no special cases.
function CompletedTable({ items }: { items: CompletedItem[] }) {
  if (items.length === 0) {
    return (
      <EmptyState
        icon={CheckCircle2}
        title="Nothing finished yet"
        description="Each run the factory completes — PRD drafted, plan written, code submitted — lands here with the agent that ran it."
      />
    );
  }

  return (
    <div className="min-w-0 rounded-lg border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-full max-w-0">Work item</TableHead>
            <TableHead className="w-40">Milestone</TableHead>
            <TableHead className="w-36">Agent</TableHead>
            {/* US-91.14: what this run cost. A $0.30 story and a $14 story
                that both read "Code submitted" are not the same event. */}
            <TableHead className="hidden w-24 lg:table-cell">Cost</TableHead>
            <TableHead className="hidden w-24 lg:table-cell">Duration</TableHead>
            <TableHead className="hidden w-24 lg:table-cell">Finished</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {items.map((i) => (
            <TableRow key={i.id}>
              <TableCell className="w-full max-w-0 min-w-0">
                <ItemCell
                  href={`/issues/${i.issueId}?from=dashboard`}
                  type={i.type}
                  displayId={i.displayId}
                  title={i.title}
                  subline={`${formatDuration(i.durationMs)} · ${i.age}${
                    i.costUsd != null ? ` · ${money(i.costUsd)}` : ""
                  }`}
                />
              </TableCell>
              <TableCell>
                <span className="rounded-full border bg-muted px-2.5 py-0.5 text-xs font-medium text-muted-foreground">
                  {i.label}
                </span>
              </TableCell>
              <TableCell className="truncate text-xs">
                {i.workerName || <span className="text-muted-foreground">—</span>}
              </TableCell>
              <TableCell className={cn(NUM, "hidden lg:table-cell")}>
                {i.costUsd != null ? money(i.costUsd) : "—"}
              </TableCell>
              <TableCell className={cn(NUM, "hidden lg:table-cell")}>
                {formatDuration(i.durationMs)}
              </TableCell>
              <TableCell className={cn(NUM, "hidden lg:table-cell")}>{i.age}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

// US-19.1: Releases owns the whole deployment lifecycle — in-flight deploys
// (which used to sit on "In the factory" with no agent and no elapsed time)
// above what is already out.
function ReleasesTable({
  releases,
  deploys,
}: {
  releases: ReleaseRow[];
  deploys: DeployRow[];
}) {
  if (releases.length === 0 && deploys.length === 0) {
    return (
      <EmptyState
        icon={Rocket}
        title="Nothing released yet"
        description="Deployments in flight and everything already out to UAT or production show here."
      />
    );
  }

  return (
    <div className="grid gap-5">
      {deploys.length > 0 && (
        <section className="grid gap-2">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            In flight ({deploys.length})
          </h3>
          <div className="min-w-0 rounded-lg border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-full max-w-0">Target</TableHead>
                  <TableHead className="w-32">Status</TableHead>
                  <TableHead className="w-24">Started</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {deploys.map((d) => (
                  <TableRow key={d.id}>
                    <TableCell className="w-full max-w-0 min-w-0 truncate">
                      {d.name}
                    </TableCell>
                    <TableCell>
                      <Badge variant="secondary">{d.status}</Badge>
                    </TableCell>
                    <TableCell className={NUM}>{d.age}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </section>
      )}

      {releases.length > 0 && (
        <section className="grid gap-2">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Releases ({releases.length})
          </h3>
          <div className="min-w-0 rounded-lg border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-40">Version</TableHead>
                  <TableHead className="w-full max-w-0">Project</TableHead>
                  <TableHead className="w-32">Status</TableHead>
                  <TableHead className="hidden w-20 lg:table-cell">Items</TableHead>
                  <TableHead className="hidden w-24 lg:table-cell">Cut</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {releases.map((r) => (
                  <TableRow key={r.id}>
                    <TableCell className="whitespace-nowrap font-mono text-sm">
                      <Link
                        href={`/projects/${r.projectId}/releases/${r.id}`}
                        className="hover:underline"
                      >
                        {r.version}
                      </Link>
                    </TableCell>
                    <TableCell className="w-full max-w-0 min-w-0">
                      <span className="flex min-w-0 flex-col">
                        <span className="truncate">{r.project}</span>
                        {/* US-35.6: Items and Cut are columns only at `lg`. */}
                        <span className="truncate text-xs text-muted-foreground lg:hidden">
                          {r.itemCount} items · cut {r.age}
                        </span>
                      </span>
                    </TableCell>
                    <TableCell>
                      <StatusBadge status={r.status as IssueStatus} />
                    </TableCell>
                    <TableCell className={cn(NUM, "hidden lg:table-cell")}>
                      {r.itemCount}
                    </TableCell>
                    <TableCell className={cn(NUM, "hidden lg:table-cell")}>{r.age}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </section>
      )}
    </div>
  );
}
