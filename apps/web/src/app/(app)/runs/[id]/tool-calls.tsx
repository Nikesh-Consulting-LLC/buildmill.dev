"use client";

// US-34.4: the proxied tool calls this run made.
//
// Readable where the work is — beside the run trace and the shell audit — so the
// record is read by someone reviewing the work rather than only during an
// incident.
//
// The call, not the payload: run, server, tool, outcome, duration, response size,
// and arguments **redacted**. Full arguments and results would put project data
// (and potentially the very secrets the catalog protects) into a table any org
// member can read. The shell audit made the same trade for the same reason.

import { useEffect, useState } from "react";

import { createClient } from "@/lib/supabase/client";

type Call = {
  id: number;
  server_name: string;
  tool: string;
  outcome: "ok" | "error" | "refused";
  error: string | null;
  duration_ms: number | null;
  response_bytes: number | null;
  arguments_redacted: unknown;
  created_at: string;
};

export function ToolCalls({ runId }: { runId: string }) {
  const [calls, setCalls] = useState<Call[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    const supabase = createClient();
    supabase
      .from("mcp_tool_calls")
      .select(
        "id, server_name, tool, outcome, error, duration_ms, response_bytes, arguments_redacted, created_at",
      )
      .eq("run_id", runId)
      .order("created_at", { ascending: false })
      .limit(100)
      .then(({ data }) => {
        if (!cancelled) setCalls((data ?? []) as unknown as Call[]);
      });
    return () => {
      cancelled = true;
    };
  }, [runId]);

  if (calls === null) return null;
  if (calls.length === 0) {
    return (
      <p className="text-xs text-muted-foreground">
        No proxied tool calls recorded for this run. A server that runs locally on
        the agent machine never passes through the factory, so its calls cannot
        appear here.
      </p>
    );
  }

  return (
    <div className="overflow-x-auto rounded-md border">
      <table className="w-full text-left text-xs">
        <thead>
          <tr className="border-b bg-muted/40">
            <th className="px-3 py-1.5 font-medium">When</th>
            <th className="px-3 py-1.5 font-medium">Server</th>
            <th className="px-3 py-1.5 font-medium">Tool</th>
            <th className="px-3 py-1.5 font-medium">Asked</th>
            <th className="px-3 py-1.5 text-right font-medium">Took</th>
          </tr>
        </thead>
        <tbody>
          {calls.map((c) => (
            <tr key={c.id} className="border-b align-top last:border-0">
              <td className="whitespace-nowrap px-3 py-1.5 text-muted-foreground">
                {new Date(c.created_at).toLocaleTimeString()}
              </td>
              <td className="px-3 py-1.5">{c.server_name}</td>
              <td className="px-3 py-1.5 font-mono">
                {c.tool}
                {c.outcome !== "ok" && (
                  <span className="ml-2 text-red-600 dark:text-red-400">
                    {c.outcome}
                    {c.error ? ` — ${c.error}` : ""}
                  </span>
                )}
              </td>
              <td className="max-w-xs truncate px-3 py-1.5 font-mono text-muted-foreground">
                {c.arguments_redacted
                  ? JSON.stringify(c.arguments_redacted)
                  : "—"}
              </td>
              <td className="whitespace-nowrap px-3 py-1.5 text-right text-muted-foreground">
                {c.duration_ms != null ? `${c.duration_ms}ms` : "—"}
                {c.response_bytes != null && ` · ${c.response_bytes}b`}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
