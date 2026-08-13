import { notFound, redirect } from "next/navigation";
import Link from "next/link";
import { ArrowLeft, Bot } from "lucide-react";
import { createClient } from "@/lib/supabase/server";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { AgentText } from "@/components/agent-text";
import { EmptyState } from "@/components/empty-state";
import { ToolCalls } from "./tool-calls";
import { workItemDisplayId } from "@/lib/work-items";
import { activityPhrase } from "@/lib/run-activity";
import { RUN_KIND_LABELS, type RunKind } from "@/lib/run-kinds";
import { InteractiveConsole } from "./interactive-console";
import { RunTraceLive } from "./run-trace-live";
import { parseStageDurations, withoutStageLines } from "@/lib/stage-durations";

/** US-15.5: one entry in the assembled trace — from the agent's stream, its
 * tool calls, or its progress notes. */
type Entry = {
  key: string;
  at: string;
  kind: string;
  content: string;
  source: "agent" | "tool" | "progress";
};

function one<T>(v: T | T[] | null | undefined): T | null {
  return Array.isArray(v) ? (v[0] ?? null) : (v ?? null);
}

const KIND_LABEL: Record<string, string> = {
  step: "Step",
  decision: "Decision",
  output: "Output",
  progress: "Progress",
  clarification: "Clarification",
  submission: "Submission",
  error: "Error",
  tool: "Tool",
};

function when(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export default async function RunDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/login");

  const { data: runData } = await supabase
    .from("runs")
    .select(
      "id, org_id, kind, status, issue_id, worker_id, error, created_at, started_at, finished_at, " +
        "resolved_settings, settings_sources, preset_name, preset_version, " +
        "stopped_reason, tokens_in, tokens_out, cost_usd, billing, " +
        "tool_surface, tool_calls_dropped, " +
        "workers(name), issues!runs_issue_org_fk(title, type, item_no, sub_no, epics(number))"
    )
    .eq("id", id)
    .maybeSingle();

  // US-38.1: how much of this run's input came back out of the provider's
  // prompt cache. Read from the usage rows rather than rolled onto `runs` —
  // one aggregate on one page does not justify two more columns and a
  // migration to keep them in step.
  const { data: cacheRows } = await supabase
    .from("llm_usage")
    .select("tokens_in, cache_read_tokens")
    .eq("run_id", id);
  const cacheTotals = (cacheRows ?? []).reduce(
    (acc, r) => ({
      tokensIn: acc.tokensIn + (r.tokens_in ?? 0),
      cached: acc.cached + (r.cache_read_tokens ?? 0),
    }),
    { tokensIn: 0, cached: 0 }
  );
  if (!runData) notFound();

  type RunRow = {
    id: string;
    org_id: string;
    kind: string;
    status: string;
    issue_id: string | null;
    // US-78.8: which agent is holding this run — the console reads its module.
    worker_id: string | null;
    error: string | null;
    created_at: string;
    started_at: string | null;
    finished_at: string | null;
    // US-32.7: what this run actually ran under, and who chose each value.
    resolved_settings: Record<string, unknown> | null;
    settings_sources: Record<string, string> | null;
    preset_name: string | null;
    preset_version: number | null;
    // US-33.2: why a run stopped, with the number it hit.
    stopped_reason: string | null;
    // US-33.1: rolled up from the append-only usage rows.
    tokens_in: number | null;
    tokens_out: number | null;
    cost_usd: number | null;
    // US-52.4: metered (gateway-priced) or subscription (deliberately
    // off-meter — zero usage rows is correct, not a gap).
    billing: string | null;
    // US-34.3/34.4: what it could reach, and how much of that is on the record.
    tool_surface: {
      granted?: { id: string; name: string; slug: string; proxied: boolean }[];
      withheld?: { name: string | null; why: string }[];
      unavailable?: { name: string | null; why: string }[];
    } | null;
    tool_calls_dropped: number | null;
    workers: { name: string } | { name: string }[] | null;
    issues:
      | {
          title: string;
          type: string;
          item_no: number | null;
          sub_no: number | null;
          epics: { number: number } | { number: number }[] | null;
        }
      | {
          title: string;
          type: string;
          item_no: number | null;
          sub_no: number | null;
          epics: { number: number } | { number: number }[] | null;
        }[]
      | null;
  };
  const run = runData as unknown as RunRow;

  const issueRow = one(run.issues);
  const worker = one(run.workers);
  const displayId = issueRow
    ? workItemDisplayId({
        type: issueRow.type,
        epicNumber: one(issueRow.epics)?.number ?? null,
        itemNo: issueRow.item_no,
        subNo: issueRow.sub_no,
      })
    : null;

  const [
    { data: traceRows },
    { data: activityRows },
    { data: noteRows },
    { data: incidentRows },
  ] = await Promise.all([
      supabase
        .from("run_trace")
        .select("id, kind, content, at")
        .eq("run_id", id)
        .order("at", { ascending: true })
        .order("id", { ascending: true }),
      supabase
        .from("run_activity")
        .select("id, tool, at")
        .eq("run_id", id)
        .order("at", { ascending: true }),
      run.issue_id
        ? supabase
            .from("issue_events")
            .select("id, payload, created_at")
            .eq("type", "progress-note")
            .filter("payload->>run_id", "eq", id)
            .order("created_at", { ascending: true })
        : Promise.resolve({ data: [] as unknown[] }),
      // US-42.2: the run's own incidents. A refused hand-back releases the run
      // back to the pool — a real state change — and until now the run it
      // happened to was the one place it could not be seen. RLS on
      // runner_incidents is `is_org_member(org_id)`, so this read is
      // org-scoped without a filter of its own.
      supabase
        .from("runner_incidents")
        .select("id, kind, message, created_at")
        .eq("run_id", id)
        .order("created_at", { ascending: true }),
    ]);

  // US-78.8: whether this run's agent holds a live ACP session. There is no
  // module column on `runs` — the module is a property of the agent, so it is
  // read from the worker's config rather than duplicated onto every run.
  const { data: runnerConfigRow } = run.worker_id
    ? await supabase
        .from("runner_config")
        .select("enabled_modules")
        .eq("worker_id", run.worker_id)
        .maybeSingle()
    : { data: null };
  const isInteractive = (
    (runnerConfigRow?.enabled_modules ?? []) as string[]
  ).includes("interactive");

  const incidents = (incidentRows ?? []) as {
    id: number;
    kind: string;
    message: string | null;
    created_at: string;
  }[];
  // Derived, not stored: a count the runner would have to remember to write is
  // a count that goes stale the first time it crashes before writing it.
  const refusedHandBacks = incidents.filter((i) =>
    /refused/i.test(i.message ?? "")
  ).length;

  // US-62.10: stage-timing bookkeeping lines are read as the dedicated
  // breakdown below, not mixed into the narration timeline.
  const stageDurations = parseStageDurations(
    (traceRows ?? []) as { kind: string; content: string | null }[],
  );
  const narrationTraceRows = withoutStageLines(
    (traceRows ?? []) as { id: number; kind: string; content: string | null; at: string }[],
  );

  const entries: Entry[] = [];
  for (const t of narrationTraceRows) {
    entries.push({
      key: `t${t.id}`,
      at: t.at as string,
      kind: t.kind as string,
      content: t.content as string,
      source: "agent",
    });
  }
  for (const a of activityRows ?? []) {
    entries.push({
      key: `a${a.id}`,
      at: a.at as string,
      kind: "tool",
      content: activityPhrase(a.tool as string),
      source: "tool",
    });
  }
  for (const n of (noteRows ?? []) as {
    id: number;
    payload: { note?: string } | null;
    created_at: string;
  }[]) {
    const note = n.payload?.note;
    if (note)
      entries.push({
        key: `n${n.id}`,
        at: n.created_at,
        kind: "progress",
        content: note,
        source: "progress",
      });
  }
  entries.sort((x, y) => (x.at < y.at ? -1 : x.at > y.at ? 1 : 0));

  const kindLabel = RUN_KIND_LABELS[run.kind as RunKind] ?? run.kind;
  const outcome =
    run.status === "succeeded"
      ? { label: "Submitted", cls: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300" }
      : run.status === "failed"
        ? { label: "Errored / released", cls: "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300" }
        : run.status === "running"
          ? { label: "Running", cls: "bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300" }
          : { label: "Queued", cls: "bg-muted text-muted-foreground" };

  return (
    <div className="flex w-full flex-col gap-6">
      {/* US-15.5: live-fill while the run works. */}
      <RunTraceLive runId={id} orgId={run.org_id as string} />

      <div className="min-w-0">
        {run.issue_id ? (
          <Link
            href={`/issues/${run.issue_id}?from=${encodeURIComponent(`/runs/${id}`)}&fromLabel=${encodeURIComponent(`${kindLabel} run`)}`}
            className="mb-1 inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
          >
            <ArrowLeft className="size-3" />
            {displayId ? `${displayId} · ${issueRow?.title}` : issueRow?.title}
          </Link>
        ) : (
          <Link
            href="/activity"
            className="mb-1 inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
          >
            <ArrowLeft className="size-3" />
            Activity
          </Link>
        )}
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-2xl font-semibold tracking-tight">
            {kindLabel} run
          </h1>
          <Badge variant="outline" className={outcome.cls}>
            {outcome.label}
          </Badge>
          {/* US-42.2: the status is not the lie — the silence was. This run
              did succeed; it also had a hand-back refused and was released
              back to the pool to get there, and both facts belong here. */}
          {refusedHandBacks > 0 && (
            <Badge
              variant="outline"
              className="bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300"
            >
              after {refusedHandBacks} refused hand-back
              {refusedHandBacks === 1 ? "" : "s"}
            </Badge>
          )}
          {worker?.name && (
            <span className="inline-flex items-center gap-1 text-sm text-muted-foreground">
              <Bot className="size-3.5" />
              {worker.name}
            </span>
          )}
        </div>
        <p className="mt-1 text-xs text-muted-foreground">
          Dispatched {when(run.created_at as string)}
          {run.finished_at && ` · ended ${when(run.finished_at as string)}`}
        </p>
      </div>

      {run.status === "failed" && run.error && (
        <Card className="border-red-200 dark:border-red-900">
          <CardHeader>
            <CardTitle className="text-base text-red-700 dark:text-red-400">
              How it ended
            </CardTitle>
          </CardHeader>
          <CardContent>
            <AgentText className="text-sm">{run.error as string}</AgentText>
          </CardContent>
        </Card>
      )}

      {/* US-42.2: no card when there is nothing to report — an empty
          "Incidents (0)" is noise on every healthy run. */}
      {incidents.length > 0 && (
        <Card className="border-amber-200 dark:border-amber-900">
          <CardHeader>
            <CardTitle className="text-base text-amber-800 dark:text-amber-400">
              Incidents ({incidents.length})
            </CardTitle>
            <CardDescription>
              The machine&apos;s problem, not the story&apos;s. A refused
              hand-back releases the run back to the pool to be claimed again,
              so a run can reach {'"'}Submitted{'"'} having been through one.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <ul className="flex flex-col gap-3">
              {incidents.map((inc) => (
                <li
                  key={inc.id}
                  className="grid grid-cols-[auto_1fr] gap-3 border-l-2 border-amber-300 pl-3 dark:border-amber-800"
                >
                  <span className="whitespace-nowrap pt-0.5 font-mono text-xs text-muted-foreground">
                    {when(inc.created_at)}
                  </span>
                  <div className="min-w-0">
                    <Badge
                      variant="secondary"
                      className="mb-1 text-[10px] uppercase tracking-wide"
                    >
                      {inc.kind}
                    </Badge>
                    <p className="whitespace-pre-wrap break-words text-sm text-foreground/90">
                      {inc.message ?? "(no message recorded)"}
                    </p>
                  </div>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      )}

      {/* US-32.7: three layers get to decide how a run executes, so a run that
          does not say which decided what is a run nobody can explain. */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Ran with</CardTitle>
          <CardDescription>
            Resolved once, server-side, when this run was claimed. These are the
            values themselves, not a reference — editing the preset afterwards
            cannot change what this run reports.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {!run.resolved_settings ||
          Object.keys(run.resolved_settings).length === 0 ? (
            <p className="text-sm text-muted-foreground">
              Nothing recorded — this run predates settings resolution, or its
              agent and org had no preset to resolve from.
            </p>
          ) : (
            <>
              {run.preset_name && (
                <p className="mb-2 text-sm">
                  Preset:{" "}
                  <span className="font-medium">{run.preset_name}</span>
                  {run.preset_version ? ` v${run.preset_version}` : ""}
                </p>
              )}
              {/* US-33.1/33.2: what it spent, against what it was allowed.
                  Visible while the run is still going, not only after. */}
              <p className="mb-2 text-sm">
                Spent:{" "}
                <span className="font-medium">
                  {/* US-52.4: a subscription run is deliberately off-meter —
                      the word, never $0.00 and never the unmetered em dash. */}
                  {run.billing === "subscription"
                    ? "Subscription (off-meter)"
                    : run.cost_usd != null
                      ? `$${Number(run.cost_usd).toFixed(4)}`
                      : "not priced"}
                </span>
                {/* US-37.2: a run has no spend ceiling of its own any more.
                    What it cost is still worth showing; what it was "allowed"
                    is now the project's budget, shown on the project. */}
                {(run.tokens_in ?? 0) + (run.tokens_out ?? 0) > 0 && (
                  <span className="text-muted-foreground">
                    {" "}
                    · {run.tokens_in ?? 0} in / {run.tokens_out ?? 0} out
                    {/* US-38.1: a cache read bills at a fraction of the input
                        rate, so on a loop that re-sends its conversation every
                        turn this share is most of what the run cost. Absent
                        rather than "0%" when nothing reported it — a run that
                        predates the split did not cache nothing. */}
                    {cacheTotals.cached > 0 && cacheTotals.tokensIn > 0 && (
                      <>
                        {" "}
                        ·{" "}
                        {Math.round(
                          (cacheTotals.cached / cacheTotals.tokensIn) * 100
                        )}
                        % of input from cache
                      </>
                    )}
                  </span>
                )}
              </p>
              {run.stopped_reason && (
                <p className="mb-2 text-sm text-amber-700 dark:text-amber-400">
                  {/* A stop is not a failure, and the surface says which. */}
                  Stopped, not failed: {run.stopped_reason}
                </p>
              )}
              <div className="overflow-x-auto rounded-md border">
                <table className="w-full text-left text-xs">
                  <thead>
                    <tr className="border-b bg-muted/40">
                      <th className="px-3 py-1.5 font-medium">Setting</th>
                      <th className="px-3 py-1.5 font-medium">Value</th>
                      <th className="px-3 py-1.5 font-medium">Chosen by</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(run.resolved_settings).map(([key, value]) => (
                      <tr key={key} className="border-b last:border-0">
                        <td className="px-3 py-1.5">{key.replaceAll("_", " ")}</td>
                        <td className="px-3 py-1.5 font-mono">{String(value)}</td>
                        <td className="px-3 py-1.5 text-muted-foreground">
                          {(run.settings_sources ?? {})[key] ?? "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </CardContent>
      </Card>

      {/* US-34.3/34.4: what this run could reach, and what it did with it. */}
      {run.tool_surface && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Tools</CardTitle>
            <CardDescription>
              Composed when the run was claimed. A server that was withheld or
              unavailable is named — a run that quietly received a smaller toolset
              is the failure this avoids.
            </CardDescription>
          </CardHeader>
          <CardContent className="grid gap-2 text-sm">
            <p className="text-xs text-muted-foreground">
              The factory&apos;s own MCP server is always present; it is how the
              run reads context and hands work back.
            </p>
            {(run.tool_surface.granted ?? []).length === 0 ? (
              <p className="text-sm text-muted-foreground">
                No other tool servers were granted — the preset this run used
                grants none.
              </p>
            ) : (
              <ul className="grid gap-1 text-sm">
                {(run.tool_surface.granted ?? []).map((g) => (
                  <li key={g.id}>
                    <span className="font-medium">{g.name}</span>{" "}
                    <span className="text-xs text-muted-foreground">
                      {g.proxied
                        ? "· through the factory proxy, so its calls are recorded"
                        : "· local to the agent machine, so its calls are not recorded"}
                    </span>
                  </li>
                ))}
              </ul>
            )}
            {[...(run.tool_surface.withheld ?? []), ...(run.tool_surface.unavailable ?? [])].map(
              (m, i) => (
                <p
                  key={`${m.name}-${i}`}
                  className="text-xs text-amber-700 dark:text-amber-400"
                >
                  {m.name ?? "a granted server"} was not available: {m.why}
                </p>
              ),
            )}
            {(run.tool_calls_dropped ?? 0) > 0 && (
              <p className="text-xs text-red-600 dark:text-red-400">
                {run.tool_calls_dropped} tool-call record(s) could not be written.
                That is different from no calls having been made — the calls
                happened and this run&apos;s record of them is incomplete.
              </p>
            )}
            <ToolCalls runId={run.id} />
          </CardContent>
        </Card>
      )}

      {stageDurations.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Stage breakdown</CardTitle>
            <CardDescription>
              Timed by the supervisor itself — checkout, invoking the CLI,
              collecting output, committing and pushing — not dependent on
              the agent narrating anything. A repair retry that ran a stage
              twice shows it summed, with the count in parentheses.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <ul className="grid gap-1.5 text-sm">
              {stageDurations.map((s) => (
                <li key={s.stage} className="flex items-center justify-between gap-2">
                  <span className="font-medium">
                    {s.stage.replace(/_/g, " ")}
                    {s.occurrences > 1 && (
                      <span className="ml-1 text-xs text-muted-foreground">
                        ({s.occurrences}×)
                      </span>
                    )}
                  </span>
                  <span className="font-mono text-xs text-muted-foreground">
                    {s.totalMs < 1000
                      ? `${s.totalMs}ms`
                      : s.totalMs < 60_000
                        ? `${(s.totalMs / 1000).toFixed(1)}s`
                        : `${Math.round(s.totalMs / 60_000)}m`}
                  </span>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      )}

      {/* US-78.8: an interactive agent holds a live session, so this run can be
          watched and steered while it works. Every other module is a one-shot
          command line with nothing to attach to. */}
      {isInteractive && run.status === "running" && (
        <InteractiveConsole runId={id} />
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Run trace ({entries.length})</CardTitle>
          {/* US-15.5: honest about coverage — say what the trace is built from,
              so an empty stretch reads as "nothing was reported", not "nothing
              happened". */}
          <CardDescription>
            Built from what the agent streamed (steps, decisions, outputs,
            errors), the tools it called, and its progress notes — in sequence.
            A quiet stretch means the agent reported nothing there, not that
            nothing happened.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {entries.length === 0 ? (
            <EmptyState
              icon={Bot}
              title="No trace yet"
              description="This run has reported nothing so far. A bare MCP agent streams little; a supervisor-driven or richly-instrumented agent streams more."
            />
          ) : (
            <ol className="flex flex-col gap-3">
              {entries.map((e) => (
                <li
                  key={e.key}
                  className="grid grid-cols-[auto_1fr] gap-3 border-l-2 border-muted pl-3"
                >
                  <span className="whitespace-nowrap pt-0.5 font-mono text-xs text-muted-foreground">
                    {when(e.at)}
                  </span>
                  <div className="min-w-0">
                    <Badge
                      variant="secondary"
                      className="mb-1 text-[10px] uppercase tracking-wide"
                    >
                      {KIND_LABEL[e.kind] ?? e.kind}
                    </Badge>
                    {e.source === "agent" && e.kind !== "tool" ? (
                      <AgentText className="text-sm">{e.content}</AgentText>
                    ) : (
                      <p className="text-sm text-foreground/90">{e.content}</p>
                    )}
                  </div>
                </li>
              ))}
            </ol>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
