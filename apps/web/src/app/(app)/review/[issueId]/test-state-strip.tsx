import Link from "next/link";
import {
  Ban,
  Bot,
  CheckCircle2,
  CircleDashed,
  FlaskConical,
  XCircle,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import type { TestGateState } from "@/lib/test-state";

/** Compact summary of the linked test cases' latest results, shown above
 * the Approve/Reject actions (us-2.6). Always visible — "no linked tests"
 * is informational and does not require an override. */
export function TestStateStrip({
  state,
  projectId,
}: {
  state: TestGateState;
  projectId?: string;
}) {
  if (state.cases.length === 0) {
    return (
      <div className="flex flex-wrap items-center gap-2 rounded-md border bg-muted/30 px-3 py-2 text-xs text-muted-foreground">
        <FlaskConical className="size-3.5" />
        <span>No linked tests</span>
        {projectId && (
          <Link
            href={`/tests?project=${projectId}`}
            className="underline-offset-2 hover:underline"
          >
            Open test library
          </Link>
        )}
      </div>
    );
  }

  // us-11.4: blocked cases are subtracted here too — before they had no
  // bucket, counted as failing, and were double-reported as broken code.
  const passing =
    state.cases.length -
    state.failing.length -
    state.blocked.length -
    state.unrun.length;
  // us-5.19: passes reported over MCP are labeled so the manager knows an
  // agent (not a person) ran them.
  const warned = new Set(
    [...state.failing, ...state.blocked, ...state.unrun].map((c) => c.id)
  );
  const agentPassing = state.cases.filter(
    (c) => !warned.has(c.id) && c.agentVerified
  ).length;
  const runIds = Array.from(
    new Set(
      state.cases
        .map((c) => c.latestRunId)
        .filter((id): id is string => Boolean(id))
    )
  );

  return (
    <div className="flex flex-col gap-2 rounded-md border bg-muted/30 px-3 py-2 text-xs">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium text-muted-foreground">Tests:</span>
        {passing > 0 && (
          <Badge
            variant="outline"
            className="gap-1 border-emerald-200 bg-emerald-100 text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950 dark:text-emerald-300"
          >
            <CheckCircle2 className="size-3" />
            {passing} passing
          </Badge>
        )}
        {agentPassing > 0 && (
          <Badge
            variant="outline"
            className="gap-1 border-violet-200 bg-violet-100 text-violet-700 dark:border-violet-900 dark:bg-violet-950 dark:text-violet-300"
          >
            <Bot className="size-3" />
            {agentPassing} agent-verified
          </Badge>
        )}
        {state.failing.length > 0 && (
          <Badge
            variant="outline"
            className="gap-1 border-red-200 bg-red-100 text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300"
          >
            <XCircle className="size-3" />
            {state.failing.length} failing
          </Badge>
        )}
        {state.blocked.length > 0 && (
          <Badge
            variant="outline"
            className="gap-1 border-amber-200 bg-amber-100 text-amber-700 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-300"
          >
            <Ban className="size-3" />
            {state.blocked.length} blocked
          </Badge>
        )}
        {state.unrun.length > 0 && (
          <Badge
            variant="outline"
            className="gap-1 border-slate-200 bg-slate-100 text-slate-600 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-400"
          >
            <CircleDashed className="size-3" />
            {state.unrun.length} unrun
          </Badge>
        )}
        {state.needsOverride && (
          <span className="text-muted-foreground">
            — approving will require a merge override.
          </span>
        )}
      </div>
      {state.failing.length > 0 && (
        <p className="text-red-700 dark:text-red-400">
          Verification failed — Reject below is prefilled with the details,
          ready to send back to the coding agent.
        </p>
      )}
      {(warned.size > 0 || runIds.length > 0) && (
        <ul className="grid gap-1 text-muted-foreground">
          {[...state.failing, ...state.blocked, ...state.unrun]
            .slice(0, 6)
            .map((c) => (
            <li key={c.id} className="flex flex-wrap items-center gap-2">
              <span className="truncate">{c.title}</span>
              <span>· {c.latestResult ?? "unrun"}</span>
              {c.agentVerified && (
                <span className="inline-flex items-center gap-1 text-violet-600 dark:text-violet-400">
                  <Bot className="size-3" />
                  agent-reported{c.workerName ? ` by ${c.workerName}` : ""}
                </span>
              )}
              {c.agentVerified && c.note && (
                <span className="max-w-[24rem] truncate italic">
                  “{c.note}”
                </span>
              )}
              {c.latestRunId && (
                <Link
                  href={`/tests/runs/${c.latestRunId}`}
                  className="underline-offset-2 hover:underline"
                >
                  view run
                </Link>
              )}
            </li>
          ))}
          {runIds.length > 0 && warned.size === 0 && (
            <li>
              Latest run:{" "}
              <Link
                href={`/tests/runs/${runIds[0]}`}
                className="underline-offset-2 hover:underline"
              >
                open
              </Link>
            </li>
          )}
        </ul>
      )}
    </div>
  );
}
