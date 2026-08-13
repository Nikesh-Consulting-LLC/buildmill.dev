import { redirect } from "next/navigation";
import { createClient } from "@/lib/supabase/server";

// US-35.2: the agent-server detail folded into the machine it was always a
// facet of. Old links carry the `agent_servers` id, so resolve it to the
// machine rather than 404ing a bookmark or a notification.
export default async function AgentServerRedirect({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const supabase = await createClient();
  const { data: host } = await supabase
    .from("agent_servers")
    .select("server_id")
    .eq("id", id)
    .maybeSingle();
  redirect(host?.server_id ? `/servers/${host.server_id}` : "/servers");
}
