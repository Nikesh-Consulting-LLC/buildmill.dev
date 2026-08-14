"use client";

import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import { computeTestGateState } from "@/lib/test-state";
import { ArtifactReader, type ArtifactKind } from "@/components/artifact-reader";
import type { PeekKind } from "./data";

// US-6.5: a lazy summary of a review — enough to decide which review to open
// first, never enough to approve. Fetches on mount (so a collapsed row costs
// nothing) via the browser client under RLS. Missing facts read "—", never a
// blank panel.
//
// US-25.1: a tile whose summary comes from an artifact opens that artifact in
// the full-screen reader. The tiles still summarize — the click is added, not
// substituted — because the number of steps is what tells you whether the plan
// is worth opening at all.

type Tile = {
  label: string;
  value: React.ReactNode;
  /** Set when the tile's fact comes from an artifact worth reading in full. */
  readerKind?: ArtifactKind;
};

function prLabel(url: string | null): string {
  if (!url) return "no PR yet";
  if (url.startsWith("simulated://")) return "simulated";
  const m = url.match(/\/pull\/(\d+)/);
  return m ? `#${m[1]}` : "open PR";
}

function countSteps(md: string): number {
  return md
    .split("\n")
    .filter((l) => /^\s*(?:[-*+]|\d+\.)\s+/.test(l)).length;
}

function firstLine(md: string): string {
  for (const raw of md.split("\n")) {
    const l = raw.trim();
    if (!l || l.startsWith("#")) continue;
    return l.replace(/^[-*+]\s+/, "").replace(/^\d+\.\s+/, "").slice(0, 160);
  }
  return "";
}

async function fetchTiles(
  issueId: string,
  peekKind: PeekKind
): Promise<Tile[]> {
  const supabase = createClient();

  if (peekKind === "code") {
    const [{ data: run }, { data: testCases }] = await Promise.all([
      supabase
        .from("runs")
        .select("lines_added, lines_removed, files_changed, pr_url")
        .eq("issue_id", issueId)
        .eq("status", "succeeded")
        .order("created_at", { ascending: false })
        .limit(1)
        .maybeSingle(),
      supabase
        .from("test_cases")
        .select("id, title")
        .eq("issue_id", issueId)
        .eq("status", "active"),
    ]);

    const caseIds = (testCases ?? []).map((c) => c.id);
    const { data: results } = caseIds.length
      ? await supabase
          .from("test_run_results")
          .select("test_case_id, result, recorded_at, test_run_id")
          .in("test_case_id", caseIds)
      : { data: [] };
    const gate = computeTestGateState(testCases ?? [], results ?? []);
    // us-11.4: subtract blocked too, or blocked cases inflate "passing".
    const passing =
      gate.cases.length -
      gate.failing.length -
      gate.blocked.length -
      gate.unrun.length;

    const diff =
      run && run.files_changed != null
        ? `+${run.lines_added ?? 0} −${run.lines_removed ?? 0} · ${run.files_changed} files`
        : "no diff metrics";
    const gateText = (testCases ?? []).length
      ? [
          passing ? `${passing} passing` : null,
          gate.failing.length ? `${gate.failing.length} failing` : null,
          gate.blocked.length ? `${gate.blocked.length} blocked` : null,
          gate.unrun.length ? `${gate.unrun.length} unrun` : null,
        ]
          .filter(Boolean)
          .join(" · ")
      : "no linked tests";

    return [
      { label: "Diff", value: diff },
      { label: "Test gate", value: gateText },
      { label: "Pull request", value: prLabel(run?.pr_url ?? null) },
    ];
  }

  if (peekKind === "plan") {
    const { data: arts } = await supabase
      .from("artifacts")
      .select("kind, content, version")
      .eq("issue_id", issueId)
      .in("kind", ["plan", "test_plan"])
      .order("version", { ascending: false });
    const plan = (arts ?? []).find((a) => a.kind === "plan");
    const hasTestPlan = (arts ?? []).some((a) => a.kind === "test_plan");
    const content = (plan?.content as string) ?? "";
    return [
      {
        label: "Plan",
        value: plan ? `${countSteps(content)} step(s)` : "no plan draft",
        readerKind: plan ? ("plan" as const) : undefined,
      },
      {
        label: "Test plan",
        value: hasTestPlan ? "present" : "none",
        readerKind: hasTestPlan ? ("test_plan" as const) : undefined,
      },
      {
        // The approach IS the plan's opening line, so reading it in full means
        // reading the plan — a third door into one room, not a third artifact.
        label: "Approach",
        value: firstLine(content) || "—",
        readerKind: plan ? ("plan" as const) : undefined,
      },
    ];
  }

  // prd
  const [{ data: arts }, { count }] = await Promise.all([
    supabase
      .from("artifacts")
      .select("content, version")
      .eq("issue_id", issueId)
      .eq("kind", "prd")
      .order("version", { ascending: false })
      .limit(1),
    supabase
      .from("issues")
      .select("id", { count: "exact", head: true })
      .eq("parent_id", issueId)
      .is("abandoned_at", null),
  ]);
  const prd = (arts ?? [])[0];
  const content = (prd?.content as string) ?? "";
  return [
    { label: "Stories", value: count ? `${count} broken out` : "not split yet" },
    {
      label: "PRD",
      value: content ? firstLine(content) || "drafted" : "no PRD draft",
      readerKind: content ? ("prd" as const) : undefined,
    },
  ];
}

export function ReviewPeek({
  issueId,
  peekKind,
  workItem,
}: {
  issueId: string;
  peekKind: PeekKind;
  /** US-25.1: named in the reader's header, so an overlay opened from a list
   * still says which item it belongs to. */
  workItem: { title: string; displayId?: string | null };
}) {
  const [tiles, setTiles] = useState<Tile[] | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetchTiles(issueId, peekKind)
      .then((t) => {
        if (!cancelled) setTiles(t);
      })
      .catch(() => {
        if (!cancelled) setError(true);
      });
    return () => {
      cancelled = true;
    };
  }, [issueId, peekKind]);

  return (
    <div className="mt-2 rounded-md border border-dashed bg-muted/30 p-3">
      {error ? (
        <p className="text-xs text-muted-foreground">
          Couldn&apos;t load the preview — open the full review instead.
        </p>
      ) : tiles === null ? (
        <p className="flex items-center gap-2 text-xs text-muted-foreground">
          <Loader2 className="size-3.5 animate-spin" /> Loading preview…
        </p>
      ) : (
        <dl className="grid grid-cols-[repeat(auto-fit,minmax(9rem,1fr))] gap-x-6 gap-y-2">
          {tiles.map((t) => (
            <div key={t.label} className="min-w-0">
              <dt className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                {t.label}
              </dt>
              <dd className="truncate text-xs font-medium tabular-nums">
                {t.readerKind ? (
                  <ArtifactReader
                    issueId={issueId}
                    kind={t.readerKind}
                    workItem={workItem}
                    triggerClassName="block w-full truncate font-medium"
                  >
                    {t.value}
                  </ArtifactReader>
                ) : (
                  t.value
                )}
              </dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}
