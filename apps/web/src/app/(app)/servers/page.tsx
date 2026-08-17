import { redirect } from "next/navigation";
import { Server } from "lucide-react";
import { createClient } from "@/lib/supabase/server";
import { resolveActiveOrg } from "@/lib/active-org";
import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { ServerDialog } from "./server-dialog";
import {
  ServerCard,
  type MachineAgents,
  type ServerRow,
  type ServerUsage,
} from "./server-card";
import type { SlotRow } from "./agent-host";
import { currentBundleHash } from "./agent-version";

/**
 * US-35.2: every machine, in one list.
 *
 * `/servers` and `/agent-servers` were two pages for one object — the second
 * one's registration flow began by picking a row from the first. What differed
 * was never the kind of machine but where it sits in a lifecycle: registered,
 * then provisioned, then running N agents. So there is one list, and a card
 * says which of those a machine is.
 */
export default async function MachinesPage() {
  const supabase = await createClient();

  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/login");

  const { orgId } = await resolveActiveOrg(supabase, user.id);
  if (!orgId) redirect("/login");

  const { data: servers } = await supabase
    .from("servers")
    .select(
      "id, name, host, port, username, auth_method, key_fingerprint, host_key_fingerprint, updated_at"
    )
    .eq("org_id", orgId)
    .order("name", { ascending: true });

  const list = (servers ?? []) as ServerRow[];
  const serverIds = list.map((s) => s.id);

  // US-10.16: which projects + deployments target each machine.
  const usageByServer = new Map<string, ServerUsage[]>();
  if (serverIds.length) {
    const { data: deps } = await supabase
      .from("deployments")
      // FK named: `app_issues` references both deployments and projects, so an
      // un-hinted embed is ambiguous (300). See deployments.py's note.
      .select(
        "server_id, project_id, name, projects!deployments_project_id_org_id_fkey(name)"
      )
      .in("server_id", serverIds);
    const tmp = new Map<string, Map<string, { name: string; deps: string[] }>>();
    for (const d of (deps ?? []) as {
      server_id: string | null;
      project_id: string;
      name: string;
      projects: { name: string } | { name: string }[] | null;
    }[]) {
      if (!d.server_id) continue;
      const proj = d.projects;
      const projName =
        (Array.isArray(proj) ? proj[0]?.name : proj?.name) ?? "Project";
      let byProj = tmp.get(d.server_id);
      if (!byProj) {
        byProj = new Map();
        tmp.set(d.server_id, byProj);
      }
      const entry = byProj.get(d.project_id) ?? { name: projName, deps: [] };
      entry.deps.push(d.name);
      byProj.set(d.project_id, entry);
    }
    for (const [sid, byProj] of tmp) {
      usageByServer.set(
        sid,
        [...byProj.entries()].map(([projectId, v]) => ({
          projectId,
          projectName: v.name,
          deployments: v.deps,
        }))
      );
    }
  }

  // US-35.2: the agent side, folded into the same list rather than living on a
  // second page. Keyed by server_id, which is what the card has.
  const { data: hosts } = await supabase
    .from("agent_servers")
    .select(
      "id, server_id, status, bundle_hash, last_probe_at, probe_error"
    )
    .eq("org_id", orgId)
    .neq("status", "removed");
  const { data: slots } = await supabase
    .from("agent_slots")
    .select("agent_server_id, desired_state, service_state")
    .eq("org_id", orgId)
    .eq("status", "active");

  const slotsByHost = new Map<string, Pick<SlotRow, "desired_state" | "service_state">[]>();
  for (const s of (slots ?? []) as unknown as SlotRow[]) {
    const bucket = slotsByHost.get(s.agent_server_id) ?? [];
    bucket.push(s);
    slotsByHost.set(s.agent_server_id, bucket);
  }

  const current = await currentBundleHash();
  const agentsByServer = new Map<string, MachineAgents>();
  for (const h of (hosts ?? []) as unknown as {
    id: string;
    server_id: string;
    status: string;
    bundle_hash: string | null;
    last_probe_at: string | null;
    probe_error: string | null;
  }[]) {
    const mine = slotsByHost.get(h.id) ?? [];
    agentsByServer.set(h.server_id, {
      // us-116.4: the card asks the host-scoped status route for "N agents
      // not ready" — the same statuses the slot cards render — instead of
      // counting the probe's service_state on its own.
      hostId: h.id,
      status: h.status,
      agentCount: mine.length,
      enabledCount: mine.filter((s) => s.desired_state === "enabled").length,
      drifted: !!current && !!h.bundle_hash && h.bundle_hash !== current,
      lastProbeAt: h.last_probe_at,
      probeError: h.probe_error,
    });
  }
  const stale = [...agentsByServer.values()].filter((a) => a.drifted).length;

  return (
    <div className="flex w-full flex-col gap-6">
      <PageHeader
        title="Machines"
        description="Every deploy target the factory can reach over SSH. Credentials are stored write-only and never shown again."
        actions={<ServerDialog orgId={orgId} />}
      />
      {/* US-57.3: agent hosting moved to platform-provisioned pools —
          Team's Add-agent wizard, never a machine registered here. */}
      {stale > 0 && (
        <p className="-mt-4 text-sm text-amber-600 dark:text-amber-400">
          {stale} {stale === 1 ? "machine is" : "machines are"} running older
          agent code.
        </p>
      )}

      {list.length === 0 ? (
        <EmptyState
          icon={Server}
          title="No machines yet"
          description="Register a machine to open an SSH terminal, browse its files, or deploy to it."
          action={<ServerDialog orgId={orgId} />}
        />
      ) : (
        <div className="grid gap-4 sm:grid-cols-2">
          {list.map((s) => (
            <ServerCard
              key={s.id}
              server={s}
              orgId={orgId}
              usage={usageByServer.get(s.id) ?? []}
              agents={agentsByServer.get(s.id) ?? null}
            />
          ))}
        </div>
      )}
    </div>
  );
}
