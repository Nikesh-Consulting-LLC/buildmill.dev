import { redirect } from "next/navigation";
import { Server } from "lucide-react";
import { createClient } from "@/lib/supabase/server";
import { EmptyState } from "@/components/empty-state";
import { ServerDialog } from "../../servers/server-dialog";
import { RegisterAgentServerDialog } from "../../servers/register-agent-dialog";
import {
  ServerCard,
  type MachineAgents,
  type ServerRow,
} from "../../servers/server-card";
import type { SlotRow } from "../../servers/agent-host";
import { currentBundleHash } from "../../servers/agent-version";

/**
 * US-57.1: the superadmin's machines are pools, not deploy targets.
 *
 * This mirrors `/servers` deliberately — a platform-owned machine is the
 * same `servers` + `agent_servers` row shape Phase 26 built, registered and
 * provisioned the same way. What differs is scope (the platform-admin org
 * only, never the caller's active org) and framing (every machine here is
 * meant to become a named, capacity-bounded pool other orgs place agents on
 * — never a deploy target).
 */
export default async function AdminMachinesPage() {
  const supabase = await createClient();

  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/login");

  // The admin layout already proved membership in a platform-admin org; this
  // resolves WHICH org that is, via the same RLS that scoped that proof —
  // never the caller's currently active org, which may be a different one.
  const { data: platformOrg } = await supabase
    .from("organizations")
    .select("id")
    .eq("is_platform_admin", true)
    .limit(1)
    .maybeSingle();
  if (!platformOrg) redirect("/workbench");
  const orgId = platformOrg.id as string;

  const { data: servers } = await supabase
    .from("servers")
    .select(
      "id, name, host, port, username, auth_method, key_fingerprint, host_key_fingerprint, updated_at"
    )
    .eq("org_id", orgId)
    .order("name", { ascending: true });

  const list = (servers ?? []) as ServerRow[];

  const { data: hosts } = await supabase
    .from("agent_servers")
    .select(
      "id, server_id, status, bundle_hash, last_probe_at, probe_error, pool_name, capacity"
    )
    .eq("org_id", orgId)
    .eq("shared", true)
    .neq("status", "removed");
  const { data: slots } = await supabase
    .from("agent_slots")
    .select("agent_server_id, org_id, desired_state, service_state")
    .in("agent_server_id", (hosts ?? []).map((h) => h.id as string))
    .eq("status", "active");

  const slotsByHost = new Map<string, Pick<SlotRow, "desired_state" | "service_state">[]>();
  // US-57.5: occupancy BY org, not just a count — the ledger the superadmin
  // asked for names who to have the "move your agent" conversation with.
  const occupantCountsByHost = new Map<string, Map<string, number>>();
  for (const s of (slots ?? []) as unknown as (SlotRow & { org_id: string })[]) {
    const bucket = slotsByHost.get(s.agent_server_id) ?? [];
    bucket.push(s);
    slotsByHost.set(s.agent_server_id, bucket);
    const counts = occupantCountsByHost.get(s.agent_server_id) ?? new Map<string, number>();
    counts.set(s.org_id, (counts.get(s.org_id) ?? 0) + 1);
    occupantCountsByHost.set(s.agent_server_id, counts);
  }

  const occupantOrgIds = [
    ...new Set((slots ?? []).map((s) => s.org_id as string)),
  ];
  const { data: occupantOrgs } = occupantOrgIds.length
    ? await supabase.from("organizations").select("id, name").in("id", occupantOrgIds)
    : { data: [] };
  const orgNameById = new Map(
    (occupantOrgs ?? []).map((o) => [o.id as string, o.name as string])
  );

  const current = await currentBundleHash();
  const agentsByServer = new Map<string, MachineAgents>();
  const poolByServer = new Map<
    string,
    {
      name: string | null;
      capacity: number | null;
      free: number;
      byOrg: { orgId: string; name: string; count: number }[];
    }
  >();
  for (const h of (hosts ?? []) as unknown as {
    id: string;
    server_id: string;
    status: string;
    bundle_hash: string | null;
    last_probe_at: string | null;
    probe_error: string | null;
    pool_name: string | null;
    capacity: number | null;
  }[]) {
    const mine = slotsByHost.get(h.id) ?? [];
    agentsByServer.set(h.server_id, {
      status: h.status,
      agentCount: mine.length,
      enabledCount: mine.filter((s) => s.desired_state === "enabled").length,
      deadCount: mine.filter(
        (s) =>
          s.desired_state === "enabled" &&
          (s.service_state === "failed" || s.service_state === "inactive")
      ).length,
      drifted: !!current && !!h.bundle_hash && h.bundle_hash !== current,
      lastProbeAt: h.last_probe_at,
      probeError: h.probe_error,
    });
    const byOrg = [...(occupantCountsByHost.get(h.id)?.entries() ?? [])]
      .map(([orgId, count]) => ({
        orgId,
        name: orgNameById.get(orgId) ?? orgId,
        count,
      }))
      .sort((a, b) => b.count - a.count);
    poolByServer.set(h.server_id, {
      name: h.pool_name,
      capacity: h.capacity,
      free: Math.max((h.capacity ?? 0) - mine.length, 0),
      byOrg,
    });
  }

  const availableForAgents = list
    .filter((s) => !agentsByServer.has(s.id))
    .map((s) => ({ id: s.id, name: s.name, host: s.host, username: s.username }));

  return (
    <div className="flex w-full flex-col gap-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Machines</h1>
          <p className="text-sm text-muted-foreground">
            Platform-provisioned hardware. Every machine here becomes a named,
            capacity-bounded pool — orgs bring their own Claude license and
            place agents on it; nobody sees a host, a port, or a credential.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {availableForAgents.length > 0 && (
            <RegisterAgentServerDialog
              orgId={orgId}
              servers={availableForAgents}
              adminPool
            />
          )}
          <ServerDialog orgId={orgId} />
        </div>
      </div>

      {list.length === 0 ? (
        <EmptyState
          icon={Server}
          title="No platform machines yet"
          description="Register a machine, name it, and give it a capacity — it becomes a pool every org can place agents on."
          action={<ServerDialog orgId={orgId} />}
        />
      ) : (
        <div className="grid gap-4 sm:grid-cols-2">
          {list.map((s) => {
            const pool = poolByServer.get(s.id);
            return (
              <div key={s.id} className="flex flex-col gap-2">
                {pool && (
                  <div className="rounded-md border bg-muted/40 px-3 py-1.5 text-xs">
                    <div className="flex items-center justify-between">
                      <span className="font-medium">{pool.name ?? "Unnamed pool"}</span>
                      <span
                        className={
                          pool.free === 0
                            ? "font-medium text-amber-600 dark:text-amber-400"
                            : "text-muted-foreground"
                        }
                      >
                        {pool.free} of {pool.capacity ?? 0} free
                      </span>
                    </div>
                    {pool.byOrg.length > 0 && (
                      <ul className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-muted-foreground">
                        {pool.byOrg.map((o) => (
                          <li key={o.orgId}>
                            {o.name}: {o.count}
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                )}
                <ServerCard
                  server={s}
                  orgId={orgId}
                  agents={agentsByServer.get(s.id) ?? null}
                  basePath="/admin/machines"
                />
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
