"use client";

import { useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import Link from "next/link";
import { cn } from "@/lib/utils";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { EmptyState } from "@/components/empty-state";
import { MarkdownView } from "@/components/markdown-view";
import {
  DEFAULT_RELEASE_SECTION,
  sectionLabel,
} from "@/lib/release-sections";
import {
  Bot,
  CheckCheck,
  ChevronRight,
  ClipboardCheck,
  Loader2,
} from "lucide-react";

export type ReleaseCase = {
  id: string;
  title: string;
  steps: string;
  expected_result: string;
  source: string;
  issue_id: string | null;
  origin_display_id: string | null;
  result: "pass" | "fail" | "blocked" | null;
  /** US-81.4: this verdict was recorded by a suite run, not a person. */
  machine?: boolean;
  /** us-101.2: where this check sits in the run, and how hard it matters. */
  section?: string | null;
  sort?: number | null;
  critical?: boolean;
};

const RESULTS = [
  { value: "pass", label: "Pass", on: "bg-emerald-600 text-white border-emerald-600" },
  { value: "fail", label: "Fail", on: "bg-red-600 text-white border-red-600" },
  { value: "blocked", label: "Blocked", on: "bg-amber-500 text-white border-amber-500" },
] as const;

/** US-21.4: the release's test set — inherited from the included work items,
 * plus the agent's regression cases — run by hand, one result each.
 *
 * `blocked` deliberately counts as not-passed for sign-off: the gate means
 * the build was tested, not that testing was attempted.
 *
 * US-74.3: a case is one line — title and the three verdicts — and opens for
 * its steps and expected result. US-74.2: "Pass all" records the not-run ones
 * in a single click. */
export function ReleaseTestCases({
  releaseId,
  version,
  cases,
  editable,
  sectionNotes = {},
}: {
  releaseId: string;
  version: string;
  cases: ReleaseCase[];
  editable: boolean;
  /** us-101.4: the agent's note for each section, from the notes declaration. */
  sectionNotes?: Record<string, string>;
}) {
  const router = useRouter();
  const [local, setLocal] = useState<Record<string, string | null>>(
    Object.fromEntries(cases.map((c) => [c.id, c.result]))
  );
  const [busy, setBusy] = useState<string | null>(null);
  const [bulkBusy, setBulkBusy] = useState(false);
  const [open, setOpen] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);

  function toggle(caseId: string) {
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(caseId)) next.delete(caseId);
      else next.add(caseId);
      return next;
    });
  }

  async function postResult(caseId: string, result: string) {
    await apiFetch(`/api/v1/releases/${releaseId}/test-cases/${caseId}/result`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ result }),
    });
  }

  async function setResult(caseId: string, result: string) {
    const prev = local[caseId] ?? null;
    setLocal((m) => ({ ...m, [caseId]: result }));
    setBusy(caseId);
    setError(null);
    try {
      await postResult(caseId, result);
      router.refresh();
    } catch (e) {
      setLocal((m) => ({ ...m, [caseId]: prev }));
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  // US-74.2: only the not-run cases are touched — a recorded fail or blocked
  // is a judgement someone made, and one click must never erase it.
  const notRun = cases.filter((c) => !local[c.id]);

  // us-101.5: the running order, grouped. `cases` arrives already sorted by
  // the page (section rank, then sort, then title), so a Map preserves it.
  const groups = (() => {
    const m = new Map<string, ReleaseCase[]>();
    for (const c of cases) {
      const key = c.section || DEFAULT_RELEASE_SECTION;
      const list = m.get(key);
      if (list) list.push(c);
      else m.set(key, [c]);
    }
    return [...m.entries()];
  })();

  async function passAll(subset?: ReleaseCase[]) {
    const targets = (subset ?? notRun).filter((c) => !local[c.id]);
    if (!targets.length) return;
    setBulkBusy(true);
    setError(null);
    let saved = 0;
    for (const c of targets) {
      try {
        await postResult(c.id, "pass");
        saved += 1;
        setLocal((m) => ({ ...m, [c.id]: "pass" }));
      } catch {
        // Leave this one not-run so the retry is obvious and safe.
      }
    }
    setBulkBusy(false);
    router.refresh();
    if (saved < targets.length) {
      setError(
        `Saved ${saved} of ${targets.length}. The rest are still not run — try again.`
      );
    }
  }

  const passSection = (subset: ReleaseCase[]) => passAll(subset);

  const counts = cases.reduce(
    (acc, c) => {
      const r = local[c.id];
      if (r === "pass") acc.pass += 1;
      else if (r === "fail") acc.fail += 1;
      else if (r === "blocked") acc.blocked += 1;
      else acc.pending += 1;
      return acc;
    },
    { pass: 0, fail: 0, blocked: 0, pending: 0 }
  );

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Test cases</CardTitle>
        <CardDescription>
          {cases.length === 0
            ? "Attached when the release reaches UAT."
            : `${counts.pass} of ${cases.length} passed` +
              (counts.fail ? ` · ${counts.fail} failed` : "") +
              (counts.blocked ? ` · ${counts.blocked} blocked` : "") +
              (counts.pending ? ` · ${counts.pending} not run` : "")}
        </CardDescription>
        {editable && notRun.length > 0 && (
          <CardAction>
            <Button
              size="sm"
              variant="outline"
              disabled={bulkBusy || busy !== null}
              onClick={() => passAll()}
            >
              {bulkBusy ? (
                <Loader2 className="size-3.5 animate-spin" />
              ) : (
                <CheckCheck className="size-3.5" />
              )}
              Pass all · {notRun.length}
            </Button>
          </CardAction>
        )}
      </CardHeader>
      <CardContent className="flex flex-col gap-2">
        {cases.length === 0 ? (
          <EmptyState
            icon={ClipboardCheck}
            title="No test cases yet"
            description="The release run attaches the included work items' cases and its own regression cases once it has deployed to UAT."
          />
        ) : (
          groups.map(([sectionKey, sectionCases]) => (
            <section key={sectionKey} className="flex flex-col gap-2">
              {/* us-101.5: the order IS the instruction — a tester works top
                  to bottom, and a section note says what this part is for. */}
              <div className="mt-2 flex flex-wrap items-baseline gap-x-2 border-b pb-1 first:mt-0">
                <h3 className="text-sm font-semibold tracking-tight">
                  {sectionLabel(sectionKey)}
                </h3>
                <span className="font-mono text-[0.7rem] text-muted-foreground">
                  {sectionCases.length}
                </span>
                {editable &&
                  sectionCases.some((c) => !local[c.id]) &&
                  groups.length > 1 && (
                    <button
                      type="button"
                      disabled={bulkBusy || busy !== null}
                      onClick={() => passSection(sectionCases)}
                      className="ml-auto text-xs text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
                    >
                      Pass this section
                    </button>
                  )}
                {sectionNotes[sectionKey] ? (
                  <p className="basis-full text-xs text-muted-foreground">
                    {sectionNotes[sectionKey]}
                  </p>
                ) : null}
              </div>
              {sectionCases.map((c) => {
            const isOpen = open.has(c.id);
            return (
              <div
                key={c.id}
                className={cn(
                  "rounded-md border",
                  // us-101.5: the checks a test suite cannot make for anyone.
                  c.critical && "border-destructive/50"
                )}
              >
                <div className="flex flex-col gap-2 px-3 py-2 sm:flex-row sm:items-center sm:justify-between">
                  <button
                    type="button"
                    onClick={() => toggle(c.id)}
                    aria-expanded={isOpen}
                    className="flex min-w-0 flex-1 items-center gap-1.5 text-left"
                  >
                    <ChevronRight
                      className={cn(
                        "size-3.5 shrink-0 text-muted-foreground transition-transform",
                        isOpen && "rotate-90"
                      )}
                    />
                    <span
                      className={cn(
                        "text-sm font-medium",
                        !isOpen && "truncate"
                      )}
                    >
                      {c.title}
                    </span>
                    {c.critical && (
                      <span
                        className="inline-flex shrink-0 items-center rounded border border-destructive/40 px-1 text-[0.65rem] font-semibold tracking-wide text-destructive uppercase"
                        title="The test suite cannot make this check for you"
                      >
                        Must
                      </span>
                    )}
                    {c.origin_display_id && (
                      <span className="hidden shrink-0 font-mono text-[0.65rem] text-muted-foreground sm:inline">
                        {c.origin_display_id}
                      </span>
                    )}
                    {c.machine && c.result && (
                      <span
                        className="inline-flex shrink-0 items-center gap-1 text-xs text-muted-foreground"
                        title="This verdict was recorded by an automated suite run"
                      >
                        <Bot className="size-3.5" />
                        auto
                      </span>
                    )}
                  </button>
                  <div className="flex shrink-0 gap-1">
                    {RESULTS.map((r) => (
                      <Button
                        key={r.value}
                        size="sm"
                        variant="outline"
                        disabled={!editable || busy === c.id || bulkBusy}
                        aria-pressed={local[c.id] === r.value}
                        className={cn(
                          "h-7 px-2 text-xs",
                          local[c.id] === r.value && r.on
                        )}
                        onClick={() => setResult(c.id, r.value)}
                      >
                        {r.label}
                      </Button>
                    ))}
                  </div>
                </div>
                {/* us-101.5: a check reads as an instruction and an
                    observation, WITHOUT being opened. A collapsed list of
                    titles is what made a release plan unreadable — a tester
                    working top to bottom should never have to click to find
                    out what to do. */}
                {(c.steps || c.expected_result) && (
                  <div className="border-t px-3 py-2 pl-8 text-xs">
                    {c.steps && (
                      <div className="text-foreground">
                        <MarkdownView className="text-xs">
                          {c.steps}
                        </MarkdownView>
                      </div>
                    )}
                    {c.expected_result && (
                      <p className="mt-1 text-muted-foreground">
                        <span
                          className={cn(
                            "mr-1.5 text-[0.65rem] font-semibold tracking-wider uppercase",
                            c.critical ? "text-destructive" : "text-primary"
                          )}
                        >
                          {c.critical ? "Must" : "Expect"}
                        </span>
                        {c.expected_result}
                      </p>
                    )}
                  </div>
                )}
                {isOpen && (
                  <div className="border-t px-3 py-2 pl-8">
                    <p className="text-xs text-muted-foreground">
                      {c.source === "agent" && !c.issue_id
                        ? "Regression case for this release"
                        : c.origin_display_id && c.issue_id ? (
                            <>
                              From{" "}
                              <Link
                                href={`/issues/${c.issue_id}?from=${encodeURIComponent(`/releases/${releaseId}`)}&fromLabel=${encodeURIComponent(version)}`}
                                className="underline-offset-4 hover:underline"
                              >
                                {c.origin_display_id}
                              </Link>
                            </>
                          ) : (
                            "Inherited from a work item"
                          )}
                    </p>
                  </div>
                )}
              </div>
            );
              })}
            </section>
          ))
        )}
        {error && (
          <p className="text-sm font-medium text-destructive">{error}</p>
        )}
      </CardContent>
    </Card>
  );
}
