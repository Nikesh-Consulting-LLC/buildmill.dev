"use client";

import { useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import { CheckCircle2, CircleDashed, SkipForward, XCircle } from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

export type ChecklistItem = {
  id: string; // test_run_results.id
  result: string;
  note: string | null;
  test_case: {
    title: string;
    steps: string;
    expected_result: string;
  };
};

const RESULT_STYLES: Record<string, string> = {
  pass: "border-emerald-500/50 bg-emerald-500/5",
  fail: "border-destructive/50 bg-destructive/5",
  skipped: "opacity-70",
};

export function RunChecklist({
  runId,
  runStatus,
  items: initialItems,
}: {
  runId: string;
  runStatus: string;
  items: ChecklistItem[];
}) {
  const router = useRouter();
  const [items, setItems] = useState(initialItems);
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const completed = runStatus === "completed";

  async function record(item: ChecklistItem, result: "pass" | "fail" | "skipped") {
    setBusy(item.id);
    const supabase = createClient();
    const note = result === "fail" ? notes[item.id]?.trim() || null : null;
    try {
      const { error } = await supabase
        .from("test_run_results")
        .update({ result, note, recorded_at: new Date().toISOString() })
        .eq("id", item.id);
      if (error) return;

      const next = items.map((i) =>
        i.id === item.id ? { ...i, result, note } : i
      );
      setItems(next);

      // Last pending result closes the run out automatically.
      if (!next.some((i) => i.result === "pending")) {
        await supabase
          .from("test_runs")
          .update({ status: "completed", completed_at: new Date().toISOString() })
          .eq("id", runId);
        router.refresh();
      }
    } finally {
      setBusy(null);
    }
  }

  async function closeOut() {
    const supabase = createClient();
    await supabase
      .from("test_runs")
      .update({ status: "completed", completed_at: new Date().toISOString() })
      .eq("id", runId);
    router.refresh();
  }

  const pending = items.filter((i) => i.result === "pending").length;

  return (
    <div className="flex flex-col gap-4">
      <ul className="grid gap-3">
        {items.map((item) => (
          <li
            key={item.id}
            className={cn(
              "rounded-lg border p-4",
              RESULT_STYLES[item.result] ?? ""
            )}
          >
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="flex items-center gap-2 font-medium">
                  {item.result === "pass" && (
                    <CheckCircle2 className="size-4 shrink-0 text-emerald-600 dark:text-emerald-400" />
                  )}
                  {item.result === "fail" && (
                    <XCircle className="size-4 shrink-0 text-destructive" />
                  )}
                  {item.result === "skipped" && (
                    <SkipForward className="size-4 shrink-0 text-muted-foreground" />
                  )}
                  {item.result === "pending" && (
                    <CircleDashed className="size-4 shrink-0 text-muted-foreground" />
                  )}
                  {item.test_case.title}
                </p>
                {item.test_case.steps && (
                  <pre className="mt-2 rounded-md bg-muted/50 p-3 text-xs leading-5 whitespace-pre-wrap">
                    {item.test_case.steps}
                  </pre>
                )}
                {item.test_case.expected_result && (
                  <p className="mt-2 text-sm text-muted-foreground">
                    <span className="font-medium text-foreground">Expected: </span>
                    {item.test_case.expected_result}
                  </p>
                )}
                {item.note && (
                  <p className="mt-2 text-sm text-destructive">
                    Note: {item.note}
                  </p>
                )}
              </div>
              {!completed && (
                <div className="flex shrink-0 flex-col items-end gap-2">
                  <div className="flex gap-1.5">
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={busy === item.id}
                      onClick={() => record(item, "pass")}
                    >
                      <CheckCircle2 className="size-4" />
                      Pass
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={busy === item.id}
                      onClick={() => record(item, "fail")}
                    >
                      <XCircle className="size-4" />
                      Fail
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={busy === item.id}
                      onClick={() => record(item, "skipped")}
                    >
                      Skip
                    </Button>
                  </div>
                  <Input
                    placeholder="Note (kept when failing)"
                    className="h-7 w-56 text-xs"
                    value={notes[item.id] ?? ""}
                    onChange={(e) =>
                      setNotes((prev) => ({ ...prev, [item.id]: e.target.value }))
                    }
                  />
                </div>
              )}
            </div>
          </li>
        ))}
      </ul>

      {!completed && pending > 0 && (
        <div className="flex items-center justify-between rounded-lg border border-dashed px-4 py-3 text-sm text-muted-foreground">
          <span>
            {pending} test{pending === 1 ? "" : "s"} still pending — results save
            as you click, so you can leave and come back.
          </span>
          <Button variant="outline" size="sm" onClick={closeOut}>
            Close out early
          </Button>
        </div>
      )}
    </div>
  );
}
