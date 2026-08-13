import { redirect } from "next/navigation";
import { createClient } from "@/lib/supabase/server";

// US-35.1: the last standalone worker page folds into the Team drawer.
//
// It rendered the same three cards the principal drawer renders — active claim,
// capabilities, recent hand-backs — from its own queries, so the two were free
// to drift; and its own breadcrumb pointed at `/workers` while its "Manage
// tokens" link pointed at `/settings/workers`, both of which have redirected to
// Team since Phase 9. A worker is not the subject: the agent is, and the agent
// is a principal.
export default async function WorkerDetailRedirect({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const supabase = await createClient();
  const { data: worker } = await supabase
    .from("workers")
    .select("principal_id")
    .eq("id", id)
    .maybeSingle();
  // No principal (or no such worker, or one this member cannot see): the roster
  // is the honest landing place. Erroring here would turn a stale bookmark into
  // a dead end.
  redirect(worker?.principal_id ? `/team?principal=${worker.principal_id}` : "/team");
}
