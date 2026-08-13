"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import {
  Bot,
  CheckCircle2,
  CircleDashed,
  Import,
  Link2,
  XCircle,
} from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

export type SuiteRunRow = {
  id: string;
  org_id: string;
  project_id: string;
  suite_id: string;
  release_id: string | null;
  trigger: string;
  commit_sha: string;
  base_url: string;
  status: string;
  tests_total: number | null;
  tests_passed: number | null;
  tests_failed: number | null;
  tests_skipped: number | null;
  log: string;
  error: string | null;
  waived_at: string | null;
  waive_reason: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
};

export type SuiteRunTest = {
  id: string;
  spec_ref: string;
  status: string;
  duration_ms: number | null;
  message: string | null;
  test_case_id: string | null;
};

type RunEvent = {
  id: number;
  run_id: string;
  phase: string;
  message: string;
  created_at: string;
};

const PHASE_STYLES: Record<string, string> = {
  preflight: "bg-cyan-100 text-cyan-700 dark:bg-cyan-950 dark:text-cyan-300",
  fetch: "bg-blue-100 text-blue-700 dark:bg-blue-950 dark:text-blue-300",
  transfer: "bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-300",
  extract: "bg-violet-100 text-violet-700 dark:bg-violet-950 dark:text-violet-300",
  script: "bg-muted text-muted-foreground",
  collect: "bg-indigo-100 text-indigo-700 dark:bg-indigo-950 dark:text-indigo-300",
  map: "bg-pink-100 text-pink-700 dark:bg-pink-950 dark:text-pink-300",
  done: "bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300",
  error: "bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-300",
};

const TERMINAL = ["succeeded", "failed", "error", "timed-out", "cancelled"];

export function suiteRunStatusStyle(status: string): string {
  if (status === "succeeded") return "text-emerald-600 dark:text-emerald-400";
  if (status === "queued" || status === "running")
    return "text-amber-600 dark:text-amber-400";
  return "text-destructive";
}

function fmtTime(iso: string) {
  return new Date(iso).toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function titleFromSpecRef(ref: string): string {
  const name = ref.includes("::") ? ref.split("::").pop()! : ref;
  return name.replace(/^test[_ ]?/i, "").replace(/[_-]+/g, " ").trim() || ref;
}

export function SuiteRunView({
  run: initialRun,
  suiteName,
  initialTests,
}: {
  run: SuiteRunRow;
  suiteName: string;
  initialTests: SuiteRunTest[];
}) {
  const router = useRouter();
  const [run, setRun] = useState(initialRun);
  const [tests, setTests] = useState(initialTests);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [adopting, setAdopting] = useState<string | null>(null);

  // Live: the run row and its event feed stream in while the pipeline works;
  // per-test rows land in bulk at collect time, so re-fetch on terminal.
  useEffect(() => {
    const supabase = createClient();
    let channel: ReturnType<typeof supabase.channel> | null = null;
    let cancelled = false;

    (async () => {
      const { data } = await supabase
        .from("suite_run_events")
        .select("id, run_id, phase, message, created_at")
        .eq("run_id", initialRun.id)
        .order("id", { ascending: true });
      if (cancelled) return;
      setEvents((data ?? []) as RunEvent[]);

      const {
        data: { session },
      } = await supabase.auth.getSession();
      if (!session || cancelled) return;
      await supabase.realtime.setAuth(session.access_token);
      channel = supabase
        .channel(`suite-run-${initialRun.id}`, { config: { private: false } })
        .on(
          "postgres_changes",
          {
            event: "UPDATE",
            schema: "public",
            table: "suite_runs",
            filter: `id=eq.${initialRun.id}`,
          },
          async (payload) => {
            const next = payload.new as SuiteRunRow;
            setRun((prev) => ({ ...prev, ...next }));
            if (TERMINAL.includes(next.status)) {
              const { data: rows } = await supabase
                .from("suite_run_tests")
                .select("id, spec_ref, status, duration_ms, message, test_case_id")
                .eq("suite_run_id", initialRun.id)
                .order("spec_ref", { ascending: true });
              setTests((rows ?? []) as SuiteRunTest[]);
            }
          }
        )
        .on(
          "postgres_changes",
          {
            event: "INSERT",
            schema: "public",
            table: "suite_run_events",
            filter: `run_id=eq.${initialRun.id}`,
          },
          (payload) => {
            const e = payload.new as RunEvent;
            setEvents((prev) =>
              prev.some((x) => x.id === e.id) ? prev : [...prev, e]
            );
          }
        )
        .subscribe();
    })();

    return () => {
      cancelled = true;
      if (channel) createClient().removeChannel(channel);
    };
  }, [initialRun.id]);

  const untracked = useMemo(
    () => tests.filter((t) => !t.test_case_id),
    [tests]
  );

  // US-82.4: adopt an untracked test into the case library — the library
  // converges on what the suite actually runs.
  async function adopt(test: SuiteRunTest) {
    setAdopting(test.id);
    const supabase = createClient();
    const { error } = await supabase.from("test_cases").insert({
      org_id: run.org_id,
      project_id: run.project_id,
      title: titleFromSpecRef(test.spec_ref),
      steps: `Automated: \`${test.spec_ref}\` in suite ${suiteName}.`,
      expected_result: "The spec passes.",
      source: "human",
      test_types: [],
      environments: ["uat"],
      execution: "automated",
      suite_id: run.suite_id,
      spec_ref: test.spec_ref,
    });
    setAdopting(null);
    if (!error) router.refresh();
  }

  const duration =
    run.started_at && run.finished_at
      ? Math.max(
          0,
          Math.round(
            (+new Date(run.finished_at) - +new Date(run.started_at)) / 1000
          )
        )
      : null;

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardHeader>
          <CardTitle className="flex flex-wrap items-center gap-2 text-base">
            <span className={cn("font-semibold", suiteRunStatusStyle(run.status))}>
              {run.status}
            </span>
            {run.waived_at && (
              <Badge variant="outline" title={run.waive_reason ?? undefined}>
                waived
              </Badge>
            )}
            <span className="text-xs font-normal text-muted-foreground">
              {run.trigger} · {run.commit_sha.slice(0, 8)} · {run.base_url}
              {duration !== null &&
                ` · ${duration >= 60 ? `${Math.floor(duration / 60)}m ${duration % 60}s` : `${duration}s`}`}
            </span>
          </CardTitle>
          {run.error && (
            <CardDescription className="text-destructive">
              {run.error}
            </CardDescription>
          )}
        </CardHeader>
        {run.tests_total !== null && (
          <CardContent className="flex items-center gap-4 text-sm">
            <span className="inline-flex items-center gap-1 text-emerald-600 dark:text-emerald-400">
              <CheckCircle2 className="size-4" />
              {run.tests_passed} passed
            </span>
            <span className="inline-flex items-center gap-1 text-destructive">
              <XCircle className="size-4" />
              {run.tests_failed} failed
            </span>
            {(run.tests_skipped ?? 0) > 0 && (
              <span className="inline-flex items-center gap-1 text-muted-foreground">
                <CircleDashed className="size-4" />
                {run.tests_skipped} skipped
              </span>
            )}
          </CardContent>
        )}
      </Card>

      {tests.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Tests</CardTitle>
            {untracked.length > 0 && (
              <CardDescription>
                {untracked.length} of {tests.length} are untracked — they exist
                in the repo but not in the case library. Adopt them so future
                runs answer a case.
              </CardDescription>
            )}
          </CardHeader>
          <CardContent>
            <ul className="grid gap-1.5">
              {tests.map((t) => (
                <li
                  key={t.id}
                  className="flex flex-wrap items-center gap-2 rounded-md border px-3 py-1.5 text-sm"
                >
                  {t.status === "pass" ? (
                    <CheckCircle2 className="size-4 shrink-0 text-emerald-600 dark:text-emerald-400" />
                  ) : t.status === "skipped" ? (
                    <CircleDashed className="size-4 shrink-0 text-muted-foreground" />
                  ) : (
                    <XCircle className="size-4 shrink-0 text-destructive" />
                  )}
                  <code className="min-w-0 flex-1 truncate text-xs">
                    {t.spec_ref}
                  </code>
                  {t.duration_ms !== null && (
                    <span className="text-xs text-muted-foreground">
                      {t.duration_ms >= 1000
                        ? `${(t.duration_ms / 1000).toFixed(1)}s`
                        : `${t.duration_ms}ms`}
                    </span>
                  )}
                  {t.test_case_id ? (
                    <Badge variant="outline" className="gap-1">
                      <Link2 className="size-3" />
                      case
                    </Badge>
                  ) : (
                    <Button
                      variant="outline"
                      size="xs"
                      disabled={adopting === t.id}
                      title="Create an automated test case answered by this spec"
                      onClick={() => adopt(t)}
                    >
                      <Import className="size-3" />
                      Adopt
                    </Button>
                  )}
                  {t.message && (
                    <p className="w-full truncate pl-6 text-xs text-destructive">
                      {t.message}
                    </p>
                  )}
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <Bot className="size-4" />
            Pipeline feed
          </CardTitle>
        </CardHeader>
        <CardContent>
          {!events.length ? (
            <p className="text-sm text-muted-foreground">No events yet.</p>
          ) : (
            <ul className="grid gap-1 font-mono text-xs">
              {events.map((e) => (
                <li key={e.id} className="flex items-start gap-2">
                  <span className="text-muted-foreground">
                    {fmtTime(e.created_at)}
                  </span>
                  <span
                    className={cn(
                      "rounded px-1.5 py-0.5",
                      PHASE_STYLES[e.phase] ?? "bg-muted text-muted-foreground"
                    )}
                  >
                    {e.phase}
                  </span>
                  <span className="min-w-0 flex-1 break-words whitespace-pre-wrap">
                    {e.message}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
