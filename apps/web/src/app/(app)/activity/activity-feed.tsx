"use client";

import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "@/lib/router-with-progress";
import {
  Activity as ActivityIcon,
  ChevronDown,
  ChevronRight,
} from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Checkbox } from "@/components/ui/checkbox";
import { EmptyState } from "@/components/empty-state";
import { formatWhen } from "@/lib/approvals";

export type ActivityRow = {
  id: string;
  projectId: string | null;
  project: string;
  kind: string;
  action: string;
  objectType: string;
  objectId: string | null;
  objectLabel: string;
  actorType: string;
  actorId: string | null;
  actorName: string;
  outcome: string;
  detail: Record<string, unknown>;
  createdAt: string;
};

const ANY = "__any__";
// US-91.8: a small window the manager steps through, not a page that grows
// under them. Ten is what was asked for.
const PAGE = 10;

const KIND_LABELS: Record<string, string> = {
  gate: "Gate decisions",
  issue: "Work item events",
  run: "Runs",
  deploy: "Deployments",
  tests: "Tests",
  learning: "Learnings",
  guideline: "Guidelines",
  content: "Content changes",
  epic: "Epics",
  release: "Release sign-offs",
};

// The tables whose changes mean the feed is stale — same realtime →
// refresh pattern the board uses, at feed granularity.
const LIVE_TABLES = ["issue_events", "runs", "deployment_runs", "approvals"];

function rowHref(row: ActivityRow): string | null {
  // US-15.5: a run row (dispatched/finished) links to that run's detail trace.
  // The feed keys run rows as `run:<run_id>:<phase>`; object_id is the issue,
  // so pull the run id from the row id and prefer the run page.
  if (row.kind === "run") {
    const m = /^run:([0-9a-f-]{36}):/.exec(row.id);
    if (m) return `/runs/${m[1]}`;
  }
  if (row.objectType === "issue" && row.objectId)
    return `/issues/${row.objectId}?from=${encodeURIComponent("/activity")}&fromLabel=${encodeURIComponent("Activity")}`;
  if (row.objectType === "deployment" && row.objectId && row.projectId)
    return `/projects/${row.projectId}/deployments/${row.objectId}`;
  if (row.kind === "content" && row.projectId)
    return `/projects/${row.projectId}/audit`;
  if (row.kind === "learning" && row.projectId)
    return `/projects/${row.projectId}`;
  if (row.kind === "guideline") return "/workbench";
  if (row.kind === "tests") return "/tests";
  if (row.kind === "epic" && row.projectId) return `/projects/${row.projectId}`;
  return null;
}

function detailLines(row: ActivityRow): [string, string][] {
  const out: [string, string][] = [];
  for (const [key, value] of Object.entries(row.detail)) {
    if (value === null || value === undefined || value === "") continue;
    out.push([
      key.replace(/_/g, " "),
      typeof value === "string" ? value : JSON.stringify(value),
    ]);
  }
  return out;
}

/** us-5.34: reverse-chronological feed with project/actor/kind filters
 * and a failures-only toggle. Success rows are one line; failure rows
 * are visually distinct and expand into the recorded detail. */
export function ActivityFeed({
  rows,
  actorNames,
  orgId,
}: {
  rows: ActivityRow[];
  actorNames: Record<string, string>;
  /** US-87.5: scopes the staleness subscriptions to this workspace. */
  orgId: string;
}) {
  const router = useRouter();
  const [actor, setActor] = useState(ANY);
  const [kind, setKind] = useState(ANY);
  const [failuresOnly, setFailuresOnly] = useState(false);
  const [page, setPage] = useState(0);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Live-ish: any change on the source tables schedules one debounced
  // server refetch.
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
      channel = supabase.channel("activity-feed", {
        config: { private: false },
      });
      for (const table of LIVE_TABLES) {
        channel = channel.on(
          "postgres_changes",
          // US-87.5: named rows — unfiltered, every run and every issue
          // event in every workspace woke this feed up.
          { event: "*", schema: "public", table, filter: `org_id=eq.${orgId}` },
          () => {
            if (refreshTimer.current) clearTimeout(refreshTimer.current);
            refreshTimer.current = setTimeout(() => router.refreshSilently(), 1500);
          }
        );
      }
      channel.subscribe();
    }

    subscribe();
    return () => {
      cancelled = true;
      if (refreshTimer.current) clearTimeout(refreshTimer.current);
      if (channel) supabase.removeChannel(channel);
    };
  }, [router, orgId]);

  function actorLabel(row: ActivityRow): string {
    if (row.actorType === "user")
      return (row.actorId && actorNames[row.actorId]) || "a member";
    return row.actorName || (row.actorType === "system" ? "factory" : "worker");
  }

  const actorItems = useMemo(
    () => [
      { label: "All actors", value: ANY },
      ...Array.from(new Set(rows.map((r) => actorLabel(r))))
        .sort()
        .map((a) => ({ label: a, value: a })),
    ],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [rows, actorNames]
  );

  const kindItems = useMemo(
    () => [
      { label: "All kinds", value: ANY },
      ...Array.from(new Set(rows.map((r) => r.kind)))
        .sort()
        .map((k) => ({ label: KIND_LABELS[k] ?? k, value: k })),
    ],
    [rows]
  );

  const filtered = useMemo(
    () =>
      rows.filter((r) => {
        if (actor !== ANY && actorLabel(r) !== actor) return false;
        if (kind !== ANY && r.kind !== kind) return false;
        if (failuresOnly && r.outcome !== "failure") return false;
        return true;
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [rows, actor, kind, failuresOnly, actorNames]
  );

  // AC5: a filter change returns to page 1; the count always describes the
  // filtered total, never the page.
  const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE));
  const current = Math.min(page, pageCount - 1);
  const from = current * PAGE;
  const shown = filtered.slice(from, from + PAGE);
  const failureCount = rows.filter((r) => r.outcome === "failure").length;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <ActivityIcon className="size-4 text-muted-foreground" />
          Feed ({filtered.length}
          {failureCount > 0 && `, ${failureCount} failure${failureCount === 1 ? "" : "s"}`}
          )
        </CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <div className="flex flex-wrap items-end gap-3">
          <div className="grid gap-1.5">
            <Label className="text-xs text-muted-foreground">Actor</Label>
            <Select
              items={actorItems}
              value={actor}
              onValueChange={(v) => {
                if (typeof v === "string") setActor(v);
                setPage(0);
              }}
            >
              <SelectTrigger size="sm" className="w-44">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {actorItems.map((i) => (
                  <SelectItem key={i.value} value={i.value}>
                    {i.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="grid gap-1.5">
            <Label className="text-xs text-muted-foreground">Kind</Label>
            <Select
              items={kindItems}
              value={kind}
              onValueChange={(v) => {
                if (typeof v === "string") setKind(v);
                setPage(0);
              }}
            >
              <SelectTrigger size="sm" className="w-44">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {kindItems.map((i) => (
                  <SelectItem key={i.value} value={i.value}>
                    {i.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <label className="flex cursor-pointer items-center gap-2 pb-1.5">
            <Checkbox
              checked={failuresOnly}
              onCheckedChange={(v) => {
                setFailuresOnly(Boolean(v));
                setPage(0);
              }}
              aria-label="Failures only"
            />
            <span className="text-xs text-muted-foreground">
              Failures only
            </span>
          </label>
        </div>

        {rows.length === 0 ? (
          <EmptyState
            icon={ActivityIcon}
            title="No activity yet"
            description="Gate decisions, runs, deployments, test reports, and content changes across the factory will show up here."
          />
        ) : shown.length === 0 ? (
          <EmptyState
            icon={ActivityIcon}
            title="Nothing matches these filters"
            description="Widen the actor or kind filter, turn off failures-only, or add projects in the filter at the top of the page."
          />
        ) : (
          <div className="grid gap-1">
            {shown.map((row) => {
              const failure = row.outcome === "failure";
              const open = Boolean(expanded[row.id]);
              const href = rowHref(row);
              const details = detailLines(row);
              return (
                <Fragment key={row.id}>
                  <div
                    className={`flex items-center gap-2 rounded-md border px-3 py-1.5 text-sm ${
                      failure
                        ? "cursor-pointer border-red-300 bg-red-50 dark:border-red-900 dark:bg-red-950/40"
                        : "border-transparent hover:bg-accent/40"
                    }`}
                    onClick={
                      failure
                        ? () =>
                            setExpanded((e) => ({ ...e, [row.id]: !open }))
                        : undefined
                    }
                  >
                    {failure ? (
                      open ? (
                        <ChevronDown className="size-3.5 shrink-0 text-red-600 dark:text-red-400" />
                      ) : (
                        <ChevronRight className="size-3.5 shrink-0 text-red-600 dark:text-red-400" />
                      )
                    ) : (
                      <span className="w-3.5 shrink-0" />
                    )}
                    <span className="w-32 shrink-0 text-xs text-muted-foreground">
                      {formatWhen(row.createdAt)}
                    </span>
                    <span className="min-w-0 flex-1 truncate">
                      <span className="font-medium">{actorLabel(row)}</span>{" "}
                      <span className={failure ? "text-red-700 dark:text-red-300" : ""}>
                        {row.action}
                      </span>
                      {row.objectLabel && (
                        <>
                          {" — "}
                          {href ? (
                            <Link
                              href={href}
                              className="underline-offset-4 hover:underline"
                              onClick={(e) => e.stopPropagation()}
                            >
                              {row.objectLabel}
                            </Link>
                          ) : (
                            row.objectLabel
                          )}
                        </>
                      )}
                    </span>
                    <span className="shrink-0 text-xs text-muted-foreground">
                      {row.project}
                    </span>
                    {row.outcome === "pending" && (
                      <Badge variant="secondary" className="shrink-0">
                        pending
                      </Badge>
                    )}
                    {failure && (
                      <Badge
                        variant="outline"
                        className="shrink-0 border-red-200 bg-red-100 text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300"
                      >
                        failed
                      </Badge>
                    )}
                  </div>
                  {failure && open && (
                    <div className="ml-8 grid gap-2 rounded-md border p-3">
                      {details.length === 0 ? (
                        <p className="text-sm text-muted-foreground">
                          No further detail was recorded.
                        </p>
                      ) : (
                        details.map(([key, value]) => (
                          <div key={key}>
                            <p className="mb-1 text-xs font-medium text-muted-foreground">
                              {key}
                            </p>
                            <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded-md bg-muted/50 p-2 text-xs">
                              {value}
                            </pre>
                          </div>
                        ))
                      )}
                      {href && (
                        <Link
                          href={href}
                          className="text-xs underline underline-offset-4"
                        >
                          Open {row.objectType || "item"} →
                        </Link>
                      )}
                    </div>
                  )}
                </Fragment>
              );
            })}
          </div>
        )}

        {filtered.length > PAGE && (
          <div className="flex items-center justify-between gap-2">
            <span className="text-xs text-muted-foreground tabular-nums">
              {from + 1}–{Math.min(from + PAGE, filtered.length)} of{" "}
              {filtered.length}
            </span>
            <span className="flex items-center gap-1">
              <Button
                variant="outline"
                size="sm"
                disabled={current === 0}
                onClick={() => setPage(current - 1)}
              >
                Previous
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={current >= pageCount - 1}
                onClick={() => setPage(current + 1)}
              >
                Next
              </Button>
            </span>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
