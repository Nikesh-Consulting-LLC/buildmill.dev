"use client";

// US-53.3: a member's detail is a page, not a drawer. This is the body of the
// Team roster's PrincipalDrawer relocated verbatim — same queries, same RPCs,
// same confirm wording — with the slide-over shell dropped. The one mechanical
// difference: the idle reason is fetched here (the drawer received it from
// TeamView's state, which a standalone page does not have).

import { useEffect, useState } from "react";
import Link from "next/link";
import { Bot, Loader2 } from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import { apiFetch } from "@/lib/api";
import { formatLastSeen } from "@/lib/format-time";
import { ROLE_LABELS } from "@/lib/permissions";
import { money } from "@/lib/budget";
import { compactTokens, formatWorkSeconds } from "@/lib/work-seconds";
import { ProjectAccess } from "../capabilities";
import { RemoveMember } from "../remove-member";
import { RUN_KIND_LABELS, type RunKind } from "@/lib/run-kinds";
import {
  formatWhen,
  principalName,
  type AgentEffort,
  type AgentSeat,
  type MemberRow,
  type WorkerRow,
} from "../team-view";

export function MemberDetail({
  orgId,
  member,
  workers,
  projects,
  slot,
  embedded,
  moduleLabel,
  tokenCount,
  effort,
  effortWindowDays,
  canManage,
  onRemoved,
}: {
  orgId: string;
  member: MemberRow;
  workers: WorkerRow[];
  projects: { id: string; name: string }[];
  /** US-27.9: the machine this agent runs on, when Build Mill owns it. The
   *  row already names it ("Pod-001 slot 5"), but that text isn't a link —
   *  the whole row is itself a toggle button, and a link can't nest inside
   *  one. This is that link, restored here instead. */
  slot?: AgentSeat | null;
  /** True when nested inline in a Team row's expand panel — the row already
   *  shows the name/kind, status, and current task, so this suppresses the
   *  repeated header and the fields the row already surfaces. */
  embedded?: boolean;
  /** us-109.1: the four facts the roster row stopped showing, because a
   *  manager looks them up rather than scans them. `null`/absent renders
   *  nothing rather than a dash — a person has no module, and an agent with
   *  no runs in the window has no effort. */
  moduleLabel?: string | null;
  tokenCount?: number;
  effort?: AgentEffort | null;
  effortWindowDays?: number;
  /** Gates the Remove action below (`manage_members`). An agent's Remove is
   *  on its settings page instead — this is the only home a person has. */
  canManage?: boolean;
  onRemoved?: () => void;
}) {
  const isAgent = member.principals?.kind === "agent";
  const [caps, setCaps] = useState<{ project_id: string }[] | null>(null);
  const [runs, setRuns] = useState<
    { id: string; kind: string; status: string; when: string; title: string; issueId: string | null; what?: string; note?: string | null }[] | null
  >(null);

  // US-62.2: this agent's own read of us-62.1's run_analytics, filtered to
  // its worker id — the fleet leaderboard and this summary come off the same
  // numbers, so a manager tuning one agent's settings never disagrees with
  // the admin view of the whole fleet. Superadmin-only endpoint: a non-admin
  // viewer simply sees nothing here, the same best-effort pattern used
  // elsewhere on this page.
  const [perf, setPerf] = useState<
    {
      key: string | null;
      label: string;
      runs: number;
      success_rate: number | null;
      avg_seconds: number | null;
      p95_seconds: number | null;
    }[]
    | null
  >(null);

  useEffect(() => {
    const workerId = workers[0]?.id;
    if (!workerId) return;
    let cancelled = false;
    apiFetch(
      `/api/v1/admin/run-analytics?group_by=kind&days=30&worker_id=${workerId}`
    )
      .then((d) => {
        if (!cancelled) setPerf(d?.rows ?? null);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [workers]);

  // Project access applies to any worker, human or agent — it's the
  // worker_capabilities row gitproxy.py's clone/fetch gate checks
  // (US-3.12/31.3/55.1). A human's own worker (their "Access token" row)
  // needs it exactly like an agent's does.
  const primaryWorker = workers[0] ?? null;

  useEffect(() => {
    const supabase = createClient();
    let cancelled = false;
    async function load() {
      // Project access keyed to the member's own worker. US-55.1: any row on
      // the pair means access — the per-kind matrix is gone.
      if (primaryWorker) {
        const { data } = await supabase
          .from("worker_capabilities")
          .select("project_id")
          .eq("worker_id", primaryWorker.id)
          .order("created_at", { ascending: true });
        if (!cancelled) setCaps(data ?? []);
      } else {
        setCaps([]);
      }
      // Recent runs across this principal's workers.
      const ids = workers.map((w) => w.id);
      if (ids.length) {
        const { data } = await supabase
          .from("runs")
          .select("id, kind, status, finished_at, issues!runs_issue_org_fk(id, title)")
          .in("worker_id", ids)
          .not("finished_at", "is", null)
          .order("finished_at", { ascending: false })
          .limit(5);
        // US-35.1: voluntary releases too. A released run loses its worker_id,
        // so the runs query above cannot see it — the retired page read them
        // from the audit trail, and dropping that would have made an agent
        // that keeps handing work back look simply inactive.
        const { data: released } = await supabase
          .from("issue_events")
          .select("id, created_at, issue_id, payload, issues(id, title)")
          .eq("org_id", orgId)
          .eq("type", "run-released")
          .in(
            "payload->>worker",
            workers.map((w) => w.name)
          )
          .order("created_at", { ascending: false })
          .limit(5);
        if (!cancelled)
          setRuns(
            [
              ...(data ?? []).map((r) => {
                const issue = r.issues as unknown as {
                  id: string;
                  title: string;
                } | null;
                return {
                  id: r.id as string,
                  kind: r.kind as string,
                  status: r.status as string,
                  when: r.finished_at as string,
                  title: issue?.title ?? "work item",
                  issueId: issue?.id ?? null,
                  what: "Handed back",
                  note: null as string | null,
                };
              }),
              ...(released ?? []).map((e) => {
                const issue = e.issues as unknown as {
                  id: string;
                  title: string;
                } | null;
                const payload = e.payload as {
                  kind?: string;
                  note?: string | null;
                };
                return {
                  id: `event-${e.id}`,
                  kind: payload.kind ?? "code",
                  status: "released",
                  when: e.created_at as string,
                  title: issue?.title ?? "work item",
                  issueId: issue?.id ?? null,
                  what: "Released back to the pool",
                  note: payload.note ?? null,
                };
              }),
            ]
              .sort((a, b) => (a.when < b.when ? 1 : -1))
              .slice(0, 5)
          );
      } else {
        setRuns([]);
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, [member.principal_id, workers, orgId]);

  return (
    <div className="flex w-full max-w-3xl flex-col gap-6">
      {!embedded && (
        <div className="min-w-0">
          <div className="flex items-center gap-1.5 text-xl font-semibold">
            {isAgent && <Bot className="size-5 text-muted-foreground" />}
            <span className="truncate">{principalName(member)}</span>
          </div>
          <p className="truncate text-xs text-muted-foreground">
            {isAgent ? "Agent" : "Person"}
            {member.principals?.email ? ` · ${member.principals.email}` : ""}
            {/* US-61.1: role is fixed and non-editable for agents — showing
                it a second time next to the "Agent" kind above would just
                repeat it. */}
            {!isAgent && ` · ${ROLE_LABELS[member.role] ?? member.role}`}
          </p>
        </div>
      )}

      {/* us-109.1: what the roster row used to carry — the CLI module, the
          token count, the join date. Fixed facts, read once, so they belong
          one click in rather than on a line that is scanned. */}
      <section className="grid gap-2">
        <h3 className="text-sm font-semibold">About</h3>
        <dl className="grid gap-x-8 gap-y-1.5 text-xs sm:grid-cols-3">
          {isAgent && (
            <div>
              <dt className="text-muted-foreground">Agent type</dt>
              <dd className="font-medium">{moduleLabel ?? "Not set"}</dd>
            </div>
          )}
          {tokenCount !== undefined && (
            <div>
              <dt className="text-muted-foreground">Tokens</dt>
              <dd className="font-medium">
                {tokenCount} active token{tokenCount === 1 ? "" : "s"}
              </dd>
            </div>
          )}
          <div>
            <dt className="text-muted-foreground">Joined</dt>
            <dd className="font-medium">{formatWhen(member.created_at)}</dd>
          </div>
        </dl>
      </section>

      {/* us-109.1: the output half of US-91.12's effort line. The row keeps
          "worked · completed" — whether this agent is earning its seat — and
          the rest is here, where there is room to say which window it covers
          rather than showing five unlabelled numbers. */}
      {isAgent && effort && (
        <section className="grid gap-2">
          <h3 className="text-sm font-semibold">
            Output{effortWindowDays ? ` (last ${effortWindowDays} days)` : ""}
          </h3>
          <dl className="grid gap-x-8 gap-y-1.5 text-xs sm:grid-cols-3">
            <div>
              <dt className="text-muted-foreground">Worked</dt>
              <dd className="font-medium tabular-nums">
                {formatWorkSeconds(effort.workSeconds)} · {effort.issuesCompleted}{" "}
                completed
              </dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Lines of code</dt>
              <dd className="font-medium tabular-nums">
                +{effort.linesAdded.toLocaleString()} −
                {effort.linesRemoved.toLocaleString()}
              </dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Tokens &amp; cost</dt>
              <dd className="font-medium tabular-nums">
                {compactTokens(effort.tokens)} · {money(effort.costUsd)}
              </dd>
            </div>
          </dl>
        </section>
      )}

      {/* US-27.9: the row already names the machine ("Pod-001 slot 5") but
          that text can't be a link — the row is itself a toggle button, and
          links can't nest inside one. This is that link, restored here. */}
      {isAgent && slot?.serverId && (
        <Link
          href={`/servers/${slot.serverId}`}
          className="flex w-fit items-center gap-1 text-xs text-muted-foreground hover:text-foreground hover:underline"
        >
          View machine ({slot.hostName}, slot {slot.slotIndex})
          <span aria-hidden>→</span>
        </Link>
      )}

      {/* Recent activity — US-35.1: hand-backs AND voluntary releases. Leads
          the panel: what an agent has actually finished is the first
          question a manager opens this for. What it's doing right now, and
          why if it's not, are already on the row above (US-63.x). */}
      <section className="grid gap-2">
        <h3 className="text-sm font-semibold">Recently done</h3>
        {runs === null ? (
          <Loader2 className="size-4 animate-spin text-muted-foreground" />
        ) : runs.length === 0 ? (
          <p className="text-xs text-muted-foreground">
            Nothing handed back yet.
          </p>
        ) : (
          <ul className="grid gap-1.5">
            {runs.map((r) => (
              <li key={r.id}>
                <Link
                  href={
                    r.issueId
                      ? `/issues/${r.issueId}?from=${encodeURIComponent(`/team/${member.principal_id}`)}&fromLabel=${encodeURIComponent(principalName(member))}`
                      : "#"
                  }
                  className="flex items-center justify-between gap-2 rounded-md border px-3 py-1.5 text-sm hover:border-ring/60"
                >
                  <span className="flex min-w-0 flex-col">
                    <span className="min-w-0 truncate">{r.title}</span>
                    {r.what && (
                      <span className="truncate text-xs text-muted-foreground">
                        {r.what}
                        {r.note ? ` — “${r.note}”` : ""}
                      </span>
                    )}
                  </span>
                  <span className="flex shrink-0 items-center gap-2 text-xs text-muted-foreground">
                    <span className="uppercase">{r.kind}</span>
                    {formatLastSeen(r.when)}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* US-62.2: this agent's own read of the fleet-wide task-run analytics
          (us-62.1) — success rate and duration by kind, last 30 days. Empty
          for a non-admin viewer (the endpoint is superadmin-gated) or an
          agent with no finished runs in the window; never an error banner. */}
      {isAgent && perf && perf.length > 0 && (
        <section className="grid gap-2">
          <h3 className="text-sm font-semibold">Performance (last 30 days)</h3>
          <div className="overflow-x-auto rounded-md border">
            <table className="w-full text-left text-xs">
              <thead>
                <tr className="border-b bg-muted/40">
                  <th className="px-3 py-1.5 font-medium">Task type</th>
                  <th className="px-3 py-1.5 text-right font-medium">Runs</th>
                  <th className="px-3 py-1.5 text-right font-medium">Success</th>
                  <th className="px-3 py-1.5 text-right font-medium">Avg</th>
                  <th className="px-3 py-1.5 text-right font-medium">p95</th>
                </tr>
              </thead>
              <tbody>
                {perf.map((p) => (
                  <tr key={p.key ?? "unattributed"} className="border-b last:border-0">
                    <td className="px-3 py-1.5">
                      {p.key ? (RUN_KIND_LABELS[p.key as RunKind] ?? p.key) : p.label}
                    </td>
                    <td className="px-3 py-1.5 text-right font-mono">{p.runs}</td>
                    <td className="px-3 py-1.5 text-right font-mono">
                      {p.success_rate == null ? "—" : `${Math.round(p.success_rate * 100)}%`}
                    </td>
                    <td className="px-3 py-1.5 text-right font-mono">
                      {p.avg_seconds == null
                        ? "—"
                        : p.avg_seconds < 90
                          ? `${p.avg_seconds}s`
                          : `${Math.round(p.avg_seconds / 60)}m`}
                    </td>
                    <td className="px-3 py-1.5 text-right font-mono">
                      {p.p95_seconds == null
                        ? "—"
                        : p.p95_seconds < 90
                          ? `${p.p95_seconds}s`
                          : `${Math.round(p.p95_seconds / 60)}m`}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {/* Project access — US-55.1: which projects, not a matrix. Applies to
          any worker: a human's own token needs a project's access row to
          git-clone/fetch through the factory remote exactly like an agent's
          does (gitproxy.py's check_capabilities gate, US-3.12/31.3). */}
      {primaryWorker && (
        <section className="grid gap-2">
          <h3 className="text-sm font-semibold">Project access</h3>
          {caps === null ? (
            <Loader2 className="size-4 animate-spin text-muted-foreground" />
          ) : (
            <ProjectAccess
              workerId={primaryWorker.id}
              orgId={orgId}
              principalId={member.principal_id}
              projects={projects}
              rows={caps}
              isAgent={isAgent}
            />
          )}
        </section>
      )}

      {/* us-109.1: a person has no settings page, so this is where their
          Remove lives now that it is off the roster row. An agent's is on
          `/team/{id}/settings` — deliberately not repeated here, so there is
          exactly one place to remove any given member. */}
      {!isAgent && canManage && onRemoved && (
        <section className="grid gap-2 border-t pt-4">
          <h3 className="text-sm font-semibold">Remove from this org</h3>
          <p className="text-xs text-muted-foreground">
            They lose access immediately and their tokens are revoked. Suspend
            instead if they may come back.
          </p>
          <RemoveMember
            orgId={orgId}
            principalId={member.principal_id}
            name={principalName(member)}
            isAgent={false}
            onRemoved={onRemoved}
            className="mt-1"
          />
        </section>
      )}
    </div>
  );
}
