"use client";

// US-91.18: merged work asks to be released.
//
// Between "merged" and "live" the dashboard said nothing — work sat in the
// default branch until the manager remembered to cut. This is the prompt,
// placed where the work already is.
//
// The claim here is the cheap one (items whose status moved to `merged` since
// the last release that SHIPPED). What a cut would actually contain is the
// commit range, and only the preview endpoint knows it — so the button opens
// the real dialog and the card says which of the two to believe.
//
// UAT amendment (2026-08-14): while a release is in flight the card IS that
// release — version linked, live status, and the items its cut actually
// snapshotted. The old shape kept the "ready to release" framing with a small
// amber chip, which read as still being asked to cut after having just cut.

import Link from "next/link";
import {
  Activity,
  CircleAlert,
  ExternalLink,
  OctagonAlert,
  Tag,
} from "lucide-react";

import { StatusBadge, type IssueStatus } from "@/components/status-badge";
import { TypeBadge, type IssueType } from "@/components/type-badge";
import { CutReleaseDialog } from "../releases/cut-release-dialog";
import {
  STOPPABLE,
  StopReleaseButton,
} from "../releases/stop-release-button";
import { duration } from "./release-liveness";
import type { ReleaseSuggestion } from "./data";

// The release lifecycle in plain words, for the line under the version.
const FLIGHT_WORDS: Record<string, string> = {
  queued: "cut — waiting for an agent to prepare it",
  running: "being prepared — notes, UAT deploy, health checks",
  "uat-deployed": "on UAT — waiting on test results and sign-off",
  "uat-signed-off": "signed off — ready to promote to production",
  promoting: "promoting to production",
};

/** us-103.4: the liveness line, and Stop when there is something to stop.
 *
 * The three readings are worded so that "abandoned" cannot be mistaken for
 * slow. That distinction is the entire point: a spinner and a corpse looked
 * identical here, and the manager only found out by reading the database. */
function Liveness({ flight }: { flight: NonNullable<ReleaseSuggestion["flight"]> }) {
  const l = flight.liveness;
  const stoppable = STOPPABLE.has(flight.status);
  if (!l) {
    // A deploy pipeline or a person owns this phase; no agent to report on.
    return stoppable ? (
      <div className="mt-2">
        <StopReleaseButton
          releaseId={flight.id}
          version={flight.version}
          status={flight.status}
        />
      </div>
    ) : null;
  }

  const agent = l.workerPrincipalId ? (
    <Link
      href={`/team/${l.workerPrincipalId}/console`}
      className="font-medium underline-offset-2 hover:underline"
    >
      {l.workerName || "an agent"}
    </Link>
  ) : (
    <span className="font-medium">{l.workerName || "an agent"}</span>
  );

  const body =
    l.reading === "unclaimed" ? (
      <>Waiting {duration(l.heldMinutes)} for an agent to pick it up.</>
    ) : l.reading === "working" ? (
      <>
        Prepared by {agent} · {duration(l.heldMinutes)}
      </>
    ) : l.reading === "silent" ? (
      <>
        {agent} has not reported for {duration(l.silentMinutes)} — it may have
        stopped.
      </>
    ) : (
      <>
        {agent} stopped reporting {duration(l.silentMinutes)} ago and its claim
        has expired. Nothing is preparing this release; the sweep will fail it.
      </>
    );

  const tone =
    l.reading === "abandoned"
      ? "border-destructive/40 bg-destructive/5 text-destructive"
      : l.reading === "silent"
        ? "border-amber-300 bg-amber-50/60 text-amber-800 dark:border-amber-900 dark:bg-amber-950/20 dark:text-amber-400"
        : "border-transparent text-muted-foreground";

  return (
    <div
      className={`mt-2 flex flex-wrap items-center justify-between gap-2 rounded-md border px-2 py-1.5 text-xs ${tone}`}
    >
      <span className="flex min-w-0 items-center gap-1.5">
        {l.reading === "abandoned" ? (
          <OctagonAlert className="size-3.5 shrink-0" />
        ) : l.reading === "silent" ? (
          <CircleAlert className="size-3.5 shrink-0" />
        ) : (
          <Activity className="size-3.5 shrink-0" />
        )}
        <span className="min-w-0">{body}</span>
      </span>
      {stoppable && (
        <StopReleaseButton
          releaseId={flight.id}
          version={flight.version}
          status={flight.status}
          size="icon-sm"
        />
      )}
    </div>
  );
}

function ItemRows({
  items,
  total,
}: {
  items: ReleaseSuggestion["items"];
  total: number;
}) {
  return (
    <ul className="mt-2 grid gap-1 border-t pt-2">
      {items.map((i) => (
        <li key={i.id} className="flex min-w-0 items-center gap-2 text-xs">
          <TypeBadge type={i.type as IssueType} />
          {i.displayId && (
            <span className="shrink-0 font-mono text-muted-foreground">
              {i.displayId}
            </span>
          )}
          <Link
            href={`/issues/${i.id}?from=workbench`}
            className="min-w-0 truncate hover:underline"
          >
            {i.title}
          </Link>
        </li>
      ))}
      {total > items.length && (
        <li className="text-xs text-muted-foreground">
          + {total - items.length} more
        </li>
      )}
    </ul>
  );
}

export function ReleaseSuggestions({
  suggestions,
}: {
  suggestions: ReleaseSuggestion[];
}) {
  // AC6: nothing to release, no card. It never becomes furniture.
  if (!suggestions.length) return null;

  return (
    <section className="grid gap-2">
      {suggestions.map((s) =>
        s.flight ? (
          // AC5 (amended): the release is in flight, so show THE RELEASE —
          // where it is in its lifecycle, and what it carries. No prompt.
          <div
            key={s.projectId}
            className="min-w-0 rounded-lg border border-sky-300/70 bg-sky-50/50 p-3 dark:border-sky-900 dark:bg-sky-950/20"
          >
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div className="min-w-0">
                <p className="flex flex-wrap items-center gap-2 text-sm font-medium">
                  <span>
                    Release{" "}
                    <Link
                      href={`/projects/${s.projectId}/releases/${s.flight.id}`}
                      className="font-mono tabular-nums underline-offset-2 hover:underline"
                    >
                      {s.flight.version}
                    </Link>
                  </span>
                  <StatusBadge status={s.flight.status as IssueStatus} />
                </p>
                <p className="text-xs text-muted-foreground">
                  {s.project}
                  {" · "}
                  {FLIGHT_WORDS[s.flight.status] ?? s.flight.status}
                </p>
              </div>

              <Link
                href={`/projects/${s.projectId}/releases/${s.flight.id}`}
                className="inline-flex h-8 shrink-0 items-center gap-1.5 rounded-md border px-3 text-sm font-medium transition-colors hover:bg-accent"
              >
                <ExternalLink className="size-4" />
                View release
              </Link>
            </div>

            {/* us-103.4: who is actually doing it, and whether they are still
                breathing. For two and a half hours on 2026-08-16 this card
                said "being prepared" about a job whose supervisor had ceased
                to exist — the sentence was true of the status and false of
                the world. */}
            <Liveness flight={s.flight} />

            {/* us-103.5: the freeze, said where the manager is standing. The
                per-item refusal already reaches every dispatch button through
                org_issue_dispatch_blocks; this is the same fact stated once,
                so it is legible even with no work item on screen. */}
            <p className="mt-2 text-xs text-muted-foreground">
              While this release is in flight, work on {s.project} can be
              written but not dispatched.
            </p>

            {/* The release's own snapshot — the authority on what it carries. */}
            <ItemRows items={s.flight.items} total={s.flight.total} />
            {s.flight.extraMerged > 0 && (
              <p className="mt-2 border-t pt-2 text-xs text-muted-foreground">
                + {s.flight.extraMerged} more merged since this cut — they ride
                the next release.
              </p>
            )}
          </div>
        ) : (
          <div
            key={s.projectId}
            className="min-w-0 rounded-lg border border-emerald-300/70 bg-emerald-50/50 p-3 dark:border-emerald-900 dark:bg-emerald-950/20"
          >
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div className="min-w-0">
                <p className="text-sm font-medium">
                  {s.capped ? `${s.total}+` : s.total} work item
                  {s.total === 1 ? "" : "s"} merged
                  {s.sinceVersion ? (
                    <>
                      {" "}
                      since{" "}
                      <span className="font-mono tabular-nums">
                        {s.sinceVersion}
                      </span>
                    </>
                  ) : (
                    " and never released"
                  )}
                </p>
                <p className="text-xs text-muted-foreground">
                  {s.project}
                  {" · "}
                  {/* AC4: two numbers must not argue. The dialog runs the real
                      preview against the commit range; this is a prompt. */}
                  <span title="Counted from work items marked merged. The cut dialog runs the real preview against the commit range and is the authority on what a release would contain.">
                    ready to release
                  </span>
                </p>
              </div>

              {s.blocker ? (
                // AC5: an invitation that fails on click is worse than none.
                <span className="shrink-0 rounded-full border border-amber-300 px-2.5 py-1 text-xs text-amber-700 dark:border-amber-900 dark:text-amber-400">
                  {s.blocker}
                </span>
              ) : (
                <CutReleaseDialog
                  projects={[{ id: s.projectId, name: s.project }]}
                  defaultProjectId={s.projectId}
                  trigger={
                    <span className="inline-flex h-8 shrink-0 items-center gap-1.5 rounded-md bg-create px-3 text-sm font-medium text-create-foreground transition-colors hover:bg-create/85">
                      <Tag className="size-4" />
                      Cut release
                    </span>
                  }
                />
              )}
            </div>

            {/* AC2: name the items. A bare count cannot be checked, and the
                manager is being asked to ship these. */}
            <ItemRows items={s.items} total={s.total} />
          </div>
        )
      )}
    </section>
  );
}
