import { createClient } from "@/lib/supabase/server";
import { AgentFailuresConsole } from "./agent-failures-console";
import type { FailureRow } from "./agent-failures-console";

/**
 * US-79.8: every agent failure, across every org — including the ones that
 * never became errors anywhere else: an agent dying holding its claim, a
 * lease expiring in silence, a heartbeat going stale.
 *
 * The admin layout already gates every /admin route on is_platform_admin();
 * the read is gated a second time in the database — list_agent_failures is a
 * security definer function whose predicate is is_platform_admin(), so the
 * member-scoped neighbours it joins for display names (org, project, issue)
 * stay member-scoped everywhere else.
 */
export default async function AgentFailuresPage() {
  const supabase = await createClient();

  const { data: failures } = await supabase.rpc("list_agent_failures");

  return (
    <div className="flex w-full flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Agent failures</h1>
        <p className="text-sm text-muted-foreground">
          Every run an agent failed, on any workspace — died holding its
          claim, stopped reporting, or came back with an error. Each row
          carries the run it failed, the agent that failed it, and the
          instructions it was working from.
        </p>
      </div>
      <AgentFailuresConsole initialFailures={(failures ?? []) as FailureRow[]} />
    </div>
  );
}
