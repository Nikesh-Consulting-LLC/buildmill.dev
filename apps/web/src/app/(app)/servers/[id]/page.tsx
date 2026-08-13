import { notFound, redirect } from "next/navigation";
import Link from "next/link";
import { ArrowLeft, KeyRound, Server as ServerIcon } from "lucide-react";
import { createClient } from "@/lib/supabase/server";
import { resolveActiveOrg } from "@/lib/active-org";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { AgentServerRow, SlotRow } from "../agent-host";
import { currentBundleHash } from "../agent-version";
import { MachineActions } from "../machine-actions";
import { ClaudeConnectCard } from "../claude-connect";
import { HostDetail, type JobRow, type SlotWithAgent } from "./host-detail";

/**
 * US-35.2: one machine — everything about the box in one place.
 *
 * There were two pages for what a manager experiences as one thing: `/servers`
 * (a card grid with no detail page at all) and `/agent-servers/[id]`. They were
 * never two kinds of object — `agent_servers.server_id` is a foreign key to
 * `servers` — but one lifecycle: registered → provisioned → running N agents.
 * So this page shows SSH identity and usage for any machine, and grows the
 * agent tabs once the machine has been provisioned.
 */
export default async function MachinePage({
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
  const { orgId } = await resolveActiveOrg(supabase, user.id);
  if (!orgId) redirect("/login");

  const { data: server } = await supabase
    .from("servers")
    .select(
      "id, name, host, port, username, auth_method, key_fingerprint, host_key_fingerprint, updated_at"
    )
    .eq("id", id)
    .maybeSingle();
  if (!server) notFound();

  // The agent side of this machine, when it has one. Absent is the normal
  // state for a deploy target that was never provisioned to run agents.
  const { data: host } = await supabase
    .from("agent_servers")
    .select("*, servers(id, name, host, port, username)")
    .eq("server_id", id)
    .neq("status", "removed")
    .maybeSingle();

  // US-52.3: whether the org holds a factory token (us-52.2) — shown on the
  // connect card because that token outranks the machine's.
  const { data: orgSub } = await supabase
    .from("claude_subscriptions")
    .select("key_last4")
    .eq("org_id", orgId)
    .maybeSingle();
  const factoryTokenSet = Boolean(orgSub);

  // What deploys here — the "used by" the list card has always shown.
  const { data: deps } = await supabase
    .from("deployments")
    // FK named: `app_issues` references both deployments and projects, so an
    // un-hinted embed is ambiguous (300). See deployments.py's note.
    .select("project_id, name, projects!deployments_project_id_org_id_fkey(name)")
    .eq("server_id", id);
  const usage = new Map<string, { name: string; deployments: string[] }>();
  for (const d of (deps ?? []) as {
    project_id: string;
    name: string;
    projects: { name: string } | { name: string }[] | null;
  }[]) {
    const proj = Array.isArray(d.projects) ? d.projects[0] : d.projects;
    const entry = usage.get(d.project_id) ?? {
      name: proj?.name ?? "Project",
      deployments: [],
    };
    entry.deployments.push(d.name);
    usage.set(d.project_id, entry);
  }

  let agents: SlotWithAgent[] = [];
  let jobs: JobRow[] = [];
  let projects: { id: string; name: string }[] = [];
  if (host) {
    const { data: slots } = await supabase
      .from("agent_slots")
      .select("*")
      .eq("agent_server_id", host.id)
      .eq("status", "active")
      .order("slot_index", { ascending: true });
    const live = (slots ?? []) as unknown as SlotRow[];

    // Presence comes from the runner's own control socket (US-10.1) — the
    // service being up and the agent being connected are different facts.
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

    const { data: projectRows } = await supabase
      .from("projects")
      .select("id, name")
      .eq("org_id", orgId)
      .order("name", { ascending: true });
    projects = projectRows ?? [];
  }

  return (
    <div className="flex w-full flex-col gap-6">
      <div>
        <Link
          href="/servers"
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
            </h1>
            <p className="mt-1 truncate font-mono text-xs text-muted-foreground">
              {server.username}@{server.host}:{server.port}
            </p>
          </div>
          <MachineActions orgId={orgId} server={server} />
        </div>
      </div>

      {/* Access — true of every machine, provisioned or not. */}
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
          <div className="flex flex-col gap-1 border-t pt-2">
            <span className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground/70">
              Used by
            </span>
            {usage.size === 0 ? (
              <p className="text-xs text-muted-foreground">
                No deployments target this machine yet.
              </p>
            ) : (
              <ul className="grid gap-1 text-xs">
                {[...usage.entries()].map(([projectId, u]) => (
                  <li key={projectId} className="flex flex-wrap items-baseline gap-1.5">
                    <Link
                      href={`/projects/${projectId}`}
                      className="font-medium text-foreground hover:underline"
                    >
                      {u.name}
                    </Link>
                    <span className="text-muted-foreground">
                      {u.deployments.join(", ")}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </CardContent>
      </Card>

      {/* Agents — the Phase 26 surface, now a section of the machine rather
          than a page of its own. */}
      {host ? (
        <>
          <HostDetail
            host={host as unknown as AgentServerRow}
            slots={agents}
            jobs={jobs}
            projects={projects}
            currentBundleHash={await currentBundleHash()}
          />
          {/* US-52.3: the machine-held Claude subscription. Only an agent
              host can hold one — a plain deploy target has no runner env. */}
          <ClaudeConnectCard
            serverId={server.id}
            connectedAt={
              (host as { claude_connected_at?: string | null })
                .claude_connected_at ?? null
            }
            factoryTokenSet={factoryTokenSet}
          />
        </>
      ) : null}
    </div>
  );
}
