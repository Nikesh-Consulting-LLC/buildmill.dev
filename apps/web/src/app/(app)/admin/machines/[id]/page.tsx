import { notFound, redirect } from "next/navigation";
import Link from "next/link";
import { ArrowLeft, Cpu, KeyRound, Server as ServerIcon } from "lucide-react";
import { createClient } from "@/lib/supabase/server";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { AgentServerRow, SlotRow } from "../../../servers/agent-host";
import { currentBundleHash } from "../../../servers/agent-version";
import { RegisterAgentServerDialog } from "../../../servers/register-agent-dialog";
import { MachineActions } from "../../../servers/machine-actions";
import { HostDetail, type JobRow, type SlotWithAgent } from "../../../servers/[id]/host-detail";

/**
 * US-57.1: one platform machine — register, provision, probe, update,
 * teardown, and its pool identity (name, capacity). Everything below reuses
 * the exact Phase 26 surface `/servers/[id]` uses (same API routes, same
 * `HostDetail` component) — a platform pool is that same machine shape,
 * owned by the platform-admin org instead of a tenant's.
 */
export default async function AdminMachinePage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const supabase = await createClient();

  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/login");

  const { data: platformOrg } = await supabase
    .from("organizations")
    .select("id")
    .eq("is_platform_admin", true)
    .limit(1)
    .maybeSingle();
  if (!platformOrg) redirect("/workbench");
  const orgId = platformOrg.id as string;

  const { data: server } = await supabase
    .from("servers")
    .select(
      "id, name, host, port, username, auth_method, key_fingerprint, host_key_fingerprint, updated_at"
    )
    .eq("id", id)
    .eq("org_id", orgId)
    .maybeSingle();
  if (!server) notFound();

  const { data: host } = await supabase
    .from("agent_servers")
    .select("*, servers(id, name, host, port, username)")
    .eq("server_id", id)
    .neq("status", "removed")
    .maybeSingle();

  let agents: SlotWithAgent[] = [];
  let jobs: JobRow[] = [];
  let occupantOrgs = 0;
  // US-57.19: slot id → owning org's name, so the superadmin can tell
  // tenants apart on a shared pool one agent at a time.
  let owners: Record<string, string> = {};
  if (host) {
    const { data: slots } = await supabase
      .from("agent_slots")
      .select("*")
      .eq("agent_server_id", host.id)
      .eq("status", "active")
      .order("slot_index", { ascending: true });
    const live = (slots ?? []) as unknown as SlotRow[];
    const occupantOrgIds = [...new Set(live.map((s) => s.org_id))];
    occupantOrgs = occupantOrgIds.length;
    const { data: occupantRows } = occupantOrgIds.length
      ? await supabase
          .from("organizations")
          .select("id, name")
          .in("id", occupantOrgIds)
      : { data: [] as { id: string; name: string }[] };
    const orgNameById = new Map(
      (occupantRows ?? []).map((o) => [o.id as string, o.name as string])
    );
    owners = Object.fromEntries(
      // An unresolvable org still labels the slot — the id beats nothing.
      live.map((s) => [s.id, orgNameById.get(s.org_id) ?? s.org_id])
    );

    const workerIds = live.map((s) => s.worker_id).filter(Boolean) as string[];
    const connected = new Set<string>();
    const runningNow = new Map<string, string>();
    if (workerIds.length) {
      const { data: sessions } = await supabase
        .from("runner_sessions")
        .select("worker_id")
        .in("worker_id", workerIds)
        .is("disconnected_at", null);
      for (const s of sessions ?? []) connected.add(s.worker_id as string);

      const { data: runs } = await supabase
        .from("runs")
        .select("worker_id, kind, issues!runs_issue_org_fk(title)")
        .in("worker_id", workerIds)
        .in("status", ["claimed", "planning", "running"]);
      for (const r of (runs ?? []) as {
        worker_id: string | null;
        kind: string;
        issues: { title: string } | { title: string }[] | null;
      }[]) {
        if (!r.worker_id) continue;
        const issue = Array.isArray(r.issues) ? r.issues[0] : r.issues;
        runningNow.set(
          r.worker_id,
          issue?.title ? `${r.kind} · ${issue.title}` : r.kind
        );
      }
    }

    const { data: paused } = await supabase
      .from("runner_config")
      .select("worker_id, paused")
      .in(
        "worker_id",
        workerIds.length ? workerIds : ["00000000-0000-0000-0000-000000000000"]
      );
    const pausedBy = new Map(
      (paused ?? []).map((p) => [p.worker_id as string, !!p.paused])
    );

    agents = live.map((slot) => ({
      ...slot,
      connected: slot.worker_id ? connected.has(slot.worker_id) : false,
      paused: slot.worker_id ? (pausedBy.get(slot.worker_id) ?? true) : true,
      currentWork: slot.worker_id ? (runningNow.get(slot.worker_id) ?? null) : null,
    }));

    const { data: jobRows } = await supabase
      .from("agent_server_jobs")
      .select("*")
      .eq("agent_server_id", host.id)
      .order("created_at", { ascending: false })
      .limit(20);
    jobs = (jobRows ?? []) as unknown as JobRow[];
  }

  return (
    <div className="flex w-full flex-col gap-6">
      <div>
        <Link
          href="/admin/machines"
          className="mb-1 inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="size-3" />
          Machines
        </Link>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="min-w-0">
            <h1 className="flex items-center gap-2 truncate text-2xl font-semibold tracking-tight">
              <ServerIcon className="size-5 shrink-0 text-muted-foreground" />
              {server.name}
              {host?.shared && (
                <Badge variant="secondary" className="font-normal">
                  Pool
                </Badge>
              )}
            </h1>
            <p className="mt-1 truncate font-mono text-xs text-muted-foreground">
              {server.username}@{server.host}:{server.port}
              {occupantOrgs > 0 &&
                ` · ${occupantOrgs} ${occupantOrgs === 1 ? "org" : "orgs"} placed here`}
            </p>
          </div>
          <MachineActions orgId={orgId} server={server} />
        </div>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <KeyRound className="size-4 text-muted-foreground" />
            Access
          </CardTitle>
        </CardHeader>
        <CardContent className="grid gap-3 text-sm">
          <div className="flex flex-col gap-1 text-xs text-muted-foreground">
            <span>
              Credential:{" "}
              {server.auth_method === "password"
                ? "Password set"
                : server.key_fingerprint
                  ? `Key set · ${server.key_fingerprint}`
                  : "Key set"}
            </span>
            <span>
              Host key:{" "}
              {server.host_key_fingerprint
                ? `Trusted · ${server.host_key_fingerprint}`
                : "Not yet verified"}
            </span>
          </div>
        </CardContent>
      </Card>

      {host ? (
        <HostDetail
          host={host as unknown as AgentServerRow}
          slots={agents}
          jobs={jobs}
          projects={[]}
          currentBundleHash={await currentBundleHash()}
          owners={owners}
        />
      ) : (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <Cpu className="size-4 text-muted-foreground" />
              Coding agents
              <Badge variant="outline" className="font-normal">
                Not set up
              </Badge>
            </CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col items-start gap-3 text-sm">
            <p className="text-muted-foreground">
              Provision this machine and give it a pool name and capacity —
              every org can then place agents on it without ever seeing this
              page.
            </p>
            <RegisterAgentServerDialog
              orgId={orgId}
              servers={[
                { id: server.id, name: server.name, host: server.host, username: server.username },
              ]}
              adminPool
            />
          </CardContent>
        </Card>
      )}
    </div>
  );
}
