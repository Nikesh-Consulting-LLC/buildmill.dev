import { redirect } from "next/navigation";
import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { createClient } from "@/lib/supabase/server";
import { resolveActiveOrg } from "@/lib/active-org";
import { loadFactoryQueue } from "./data";
import { QueueView } from "./queue-view";

/** US-15.2: the queue the "In the factory" card summarizes — every claimable
 * and in-progress run, grouped by project, in worker-pull order, with the
 * manager's own controls to reorder and pause. */
export default async function FactoryQueuePage() {
  const supabase = await createClient();

  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/login");

  const { orgId } = await resolveActiveOrg(supabase, user.id);
  const groups = orgId ? await loadFactoryQueue(supabase, orgId) : [];

  return (
    <div className="flex w-full flex-col gap-6">
      <div>
        <Link
          href="/workbench"
          className="mb-2 inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="size-3.5" />
          Workbench
        </Link>
        <h1 className="text-2xl font-semibold tracking-tight">Factory queue</h1>
        <p className="text-sm text-muted-foreground">
          Everything the factory is holding or working, in the order it will
          be worked. Drag to reorder; pause anything you are not ready for.
        </p>
      </div>

      <QueueView groups={groups} orgId={orgId ?? ""} />
    </div>
  );
}
