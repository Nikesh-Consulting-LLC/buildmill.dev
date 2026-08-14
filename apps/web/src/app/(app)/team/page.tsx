import { redirect } from "next/navigation";
import { requireOrg } from "../settings/require-org";
import { loadOrgCapabilities } from "@/lib/permissions";
import { factoryMcpUrl, factoryRemoteUrl } from "@/lib/factory-git";
import { buildSnippets } from "../settings/worker-connect";
import { PageHeader } from "@/components/page-header";
import {
  TeamView,
  type AgentEffort,
  type MachineOption,
  type MemberRow,
  type TeamKpis,
  type WorkerRow,
} from "./team-view";

export default async function TeamPage({
  searchParams,
}: {
  searchParams: Promise<{ principal?: string; expand?: string }>;
}) {
  const { principal, expand } = await searchParams;
  // The old `?principal=` drawer deep link, and the retired standalone member
  // page, both land here now — `expand` opens that row's detail inline
  // instead of a second page.
  if (principal) redirect(`/team?expand=${principal}`);
  const { supabase, user, orgId } = await requireOrg();
  const { can } = await loadOrgCapabilities(supabase, orgId, user.id);

  const [
    { data: org },
    { data: members },
    { data: workers },
    { data: claims },
    { data: projects },
    { data: liveSessions },
    { data: recentIncidents },
    { data: myPrincipal },
  ] = await Promise.all([
    supabase
      .from("organizations")
      .select("name, shortname, max_agents")
      .eq("id", orgId)
      .maybeSingle(),
    supabase
      .from("organization_members")
      .select(
        "principal_id, user_id, role, status, created_at, principals(kind, email, display_name)"
      )
      .eq("org_id", orgId)
      .order("created_at", { ascending: true }),
    supabase
      .from("workers")
      .select(
        "id, name, type, user_id, principal_id, token_last4, status, last_seen_at, created_at, project_id"
      )
      .eq("org_id", orgId)
      .order("created_at", { ascending: true }),
    supabase
      .from("runs")
      .select(
        "id, worker_id, kind, claimed_at, last_heartbeat_at, issue_id, issues!runs_issue_org_fk(title, projects(name))"
      )
      .eq("org_id", orgId)
      .eq("status", "running")
      .not("worker_id", "is", null),
    supabase
      .from("projects")
      .select("id, name, slug")
      .eq("org_id", orgId)
      .is("archived_at", null)
      .order("name", { ascending: true }),
    // US-10.12: live runner presence + recent faults, for the roster status pills.
    supabase.from("runner_sessions").select("worker_id").is("disconnected_at", null),
    supabase
      .from("runner_incidents")
      .select("worker_id")
      .gte("created_at", new Date(Date.now() - 3600_000).toISOString()),
    // Which row is "me" — the merged detail panel's Router token section
    // reads differently for your own token vs. someone else's.
    supabase.from("principals").select("id").eq("auth_user_id", user.id).maybeSingle(),
  ]);

  const shortname = org?.shortname ?? "";
  const snippets = buildSnippets({
    mcpUrl: factoryMcpUrl(),
    gitCloneUrl: factoryRemoteUrl(shortname, "<project-slug>"),
  });

  type ClaimIssue = {
    title: string;
    projects: { name?: string } | { name?: string }[] | null;
  } | null;
  const runningClaims = (claims ?? []).map((c) => {
    const issue = c.issues as unknown as ClaimIssue;
    const proj = Array.isArray(issue?.projects) ? issue?.projects[0] : issue?.projects;
    return {
    worker_id: c.worker_id as string,
    // US-35.3: the run and the item it is on, so the row can link to the run
    // and name the project rather than just the work item.
    run_id: c.id as string,
    issue_id: (c.issue_id as string | null) ?? null,
    project: proj?.name ?? null,
    title: issue?.title ?? "work item",
    kind: c.kind as string,
    // US-13.7: liveness for the Live tab — headless MCP workers have no
    // socket session, but a claim heartbeat says they're at work.
    claimed_at: (c.claimed_at as string | null) ?? null,
    last_heartbeat_at: (c.last_heartbeat_at as string | null) ?? null,
    };
  });

  // US-35.3: what each person has on their plate. `issues.assignee_id` points
  // at a principal, so people and agents are counted the same way — an agent
  // can be assigned work too, and the Live tab should not pretend otherwise.
  const OPEN_EXCLUDED = ["done", "merged", "cancelled", "released"];
  const { data: assigned } = await supabase
    .from("issues")
    .select("assignee_id")
    .eq("org_id", orgId)
    .not("assignee_id", "is", null)
    .not("status", "in", `(${OPEN_EXCLUDED.join(",")})`);
  const assignedByPrincipal: Record<string, number> = {};
  for (const a of assigned ?? []) {
    const pid = a.assignee_id as string;
    assignedByPrincipal[pid] = (assignedByPrincipal[pid] ?? 0) + 1;
  }

  // US-10.12: per-agent runner presence + derived health for the roster.
  const workerToPrincipal = new Map<string, string>();
  for (const w of workers ?? []) {
    if (w.principal_id) workerToPrincipal.set(w.id as string, w.principal_id as string);
  }
  const onlinePrincipals = new Set<string>();
  for (const s of liveSessions ?? []) {
    const p = workerToPrincipal.get(s.worker_id as string);
    if (p) onlinePrincipals.add(p);
  }
  const incidentByPrincipal: Record<string, number> = {};
  for (const i of recentIncidents ?? []) {
    const p = workerToPrincipal.get(i.worker_id as string);
    if (p) incidentByPrincipal[p] = (incidentByPrincipal[p] ?? 0) + 1;
  }
  const runnerStatus: Record<
    string,
    { online: boolean; health: "healthy" | "degraded" | "unhealthy" }
  > = {};
  for (const w of workers ?? []) {
    if (!w.principal_id || w.type !== "autonomous") continue;
    const pid = w.principal_id as string;
    const n = incidentByPrincipal[pid] ?? 0;
    runnerStatus[pid] = {
      online: onlinePrincipals.has(pid),
      health: n >= 3 ? "unhealthy" : n >= 1 ? "degraded" : "healthy",
    };
  }

  // US-26.10: which machine each managed agent runs on. An agent whose
  // lifecycle the app owns is a different thing from one someone installed by
  // hand — you can restart the first from the app and not the second — so the
  // roster says which, rather than leaving the missing controls unexplained.
  // US-55.6: `id`/`worker_id` ride along too, so the roster can offer the
  // Claude Terminal action without a trip to the agent's own page first.
  const { data: agentSlots } = await supabase
    .from("agent_slots")
    .select(
      "id, principal_id, slot_index, agent_server_id, worker_id, agent_servers(id, servers(id, name))"
    )
    .eq("org_id", orgId)
    .eq("status", "active");
  // US-35.2: `hostId` still keys the fleet API's slot actions; `serverId` is
  // what the UI links to, now that the machine owns the page. Keeping both is
  // deliberate — collapsing them would break one caller or the other.
  const hostByPrincipal: Record<
    string,
    { hostId: string; serverId: string | null; hostName: string; slotIndex: number; slotId: string }
  > = {};
  type Embedded = {
    servers?: { id?: string; name?: string } | { id?: string; name?: string }[] | null;
  };
  const workerIdByPrincipal: Record<string, string> = {};
  for (const s of (agentSlots ?? []) as unknown as {
    id: string;
    principal_id: string | null;
    slot_index: number;
    agent_server_id: string;
    worker_id: string | null;
    // PostgREST embeds arrive as an object or a one-element array depending on
    // the relationship the type generator inferred; unwrap both.
    agent_servers: Embedded | Embedded[] | null;
  }[]) {
    if (!s.principal_id) continue;
    const host = Array.isArray(s.agent_servers) ? s.agent_servers[0] : s.agent_servers;
    const srv = Array.isArray(host?.servers) ? host?.servers[0] : host?.servers;
    hostByPrincipal[s.principal_id] = {
      hostId: s.agent_server_id,
      serverId: srv?.id ?? null,
      hostName: srv?.name ?? "a machine",
      slotIndex: s.slot_index,
      slotId: s.id,
    };
    if (s.worker_id) workerIdByPrincipal[s.principal_id] = s.worker_id;
  }

  // US-55.6: the Claude Terminal action only exists for a subscription-billed
  // agent — an API-billed one has a metered gateway key that outranks any
  // OAuth login, so there's no session to hand over.
  // Also reads enabled_modules, so the Live tab can say what kind of agent
  // (Claude Code, OpenCode, ...) each one is. Scoped to every autonomous
  // worker, not just ones with a fleet slot, so a self-hosted agent's module
  // shows too.
  const autonomousWorkerIds = (workers ?? [])
    .filter((w) => w.type === "autonomous")
    .map((w) => w.id as string);
  const claudeBillingByPrincipal: Record<string, string | null> = {};
  const moduleByPrincipal: Record<string, string[]> = {};
  if (autonomousWorkerIds.length) {
    const { data: configs } = await supabase
      .from("runner_config")
      .select("worker_id, claude_billing, enabled_modules")
      .in("worker_id", autonomousWorkerIds);
    for (const c of configs ?? []) {
      const pid = workerToPrincipal.get(c.worker_id as string);
      if (!pid) continue;
      claudeBillingByPrincipal[pid] = (c.claude_billing as string | null) ?? null;
      moduleByPrincipal[pid] = (c.enabled_modules as string[] | null) ?? [];
    }
  }

  // US-78.11: the run each agent is holding right now, keyed by principal, so
  // the roster's CLI button can glow while its agent is working. Derived from
  // the claims already loaded above — no extra query, and it cannot disagree
  // with the "working on" line beside it, which reads the same rows.
  const runningRunByPrincipal: Record<string, string> = {};
  for (const c of claims ?? []) {
    const pid = workerToPrincipal.get(c.worker_id as string);
    if (pid && c.id) runningRunByPrincipal[pid] = c.id as string;
  }

  // US-91.12: the three numbers on top, and each agent's own totals — read
  // from the us-91.11 rollup, so the page's cost does not grow with the number
  // of runs the workspace has ever done. One query for the window, one count
  // for each live figure.
  const WINDOW_DAYS = 30;
  const windowStart = new Date(Date.now() - WINDOW_DAYS * 86_400_000)
    .toISOString()
    .slice(0, 10);
  const [{ data: effortRows }, completedCount, queuedCount] = await Promise.all([
    supabase
      .from("agent_effort_daily")
      .select(
        "worker_id, work_seconds, issues_completed, lines_added, lines_removed, tokens_in, tokens_out, cost_usd"
      )
      .eq("org_id", orgId)
      .gte("day", windowStart),
    supabase
      .from("issues")
      .select("id", { count: "exact", head: true })
      .eq("org_id", orgId)
      .eq("status", "merged")
      .gte("status_changed_at", `${windowStart}T00:00:00Z`),
    supabase
      .from("issues")
      .select("id", { count: "exact", head: true })
      .eq("org_id", orgId)
      .in("status", ["queued", "running"]),
  ]);

  const effortByPrincipal: Record<string, AgentEffort> = {};
  let totalWorkSeconds = 0;
  for (const row of effortRows ?? []) {
    totalWorkSeconds += Number(row.work_seconds ?? 0);
    const pid = workerToPrincipal.get(row.worker_id as string);
    if (!pid) continue;
    const acc = (effortByPrincipal[pid] ??= {
      workSeconds: 0,
      issuesCompleted: 0,
      linesAdded: 0,
      linesRemoved: 0,
      tokens: 0,
      costUsd: 0,
    });
    acc.workSeconds += Number(row.work_seconds ?? 0);
    acc.issuesCompleted += Number(row.issues_completed ?? 0);
    acc.linesAdded += Number(row.lines_added ?? 0);
    acc.linesRemoved += Number(row.lines_removed ?? 0);
    acc.tokens += Number(row.tokens_in ?? 0) + Number(row.tokens_out ?? 0);
    acc.costUsd += Number(row.cost_usd ?? 0);
  }

  const kpis: TeamKpis = {
    windowDays: WINDOW_DAYS,
    completed: completedCount.count ?? 0,
    queued: queuedCount.count ?? 0,
    workSeconds: totalWorkSeconds,
  };

  // US-35.1: the machines an agent can be given a seat on, for the Add-agent
  // picker. Only provisioned ones: a registered-but-not-provisioned machine has
  // no supervisor to run an agent, so offering it would be offering a failure.
  // US-53.2: `modules` and `claude_connected_at` ride along so the wizard can
  // filter its What step to what the machine can run, and answer billing
  // readiness before any slot exists.
  const { data: hosts } = await supabase
    .from("agent_servers")
    .select("id, status, last_probe_at, modules, claude_connected_at, servers(id, name)")
    .eq("org_id", orgId)
    .neq("status", "new");
  const slotCountByHost = new Map<string, number>();
  for (const s of (agentSlots ?? []) as unknown as { agent_server_id: string }[]) {
    slotCountByHost.set(
      s.agent_server_id,
      (slotCountByHost.get(s.agent_server_id) ?? 0) + 1
    );
  }
  const machines: MachineOption[] = ((hosts ?? []) as unknown as {
    id: string;
    last_probe_at: string | null;
    modules: string[] | null;
    claude_connected_at: string | null;
    servers: { id?: string; name?: string } | { id?: string; name?: string }[] | null;
  }[]).map((h) => {
    const srv = Array.isArray(h.servers) ? h.servers[0] : h.servers;
    return {
      hostId: h.id,
      serverId: srv?.id ?? null,
      name: srv?.name ?? "a machine",
      agentCount: slotCountByHost.get(h.id) ?? 0,
      lastProbeAt: h.last_probe_at,
      modules: h.modules ?? [],
      claudeConnectedAt: h.claude_connected_at,
    };
  });

  return (
    <div className="flex w-full flex-col gap-6">
      <PageHeader
        title="Team"
        description={`Everyone in ${org?.name ?? "your org"} — people and agents — their roles, access, and what they're doing.`}
      />
      <TeamView
        orgId={orgId}
        canManage={can("manage_members")}
        members={(members ?? []) as unknown as MemberRow[]}
        workers={(workers ?? []) as unknown as WorkerRow[]}
        claims={runningClaims}
        projects={(projects ?? []) as { id: string; name: string; slug: string }[]}
        orgShortname={shortname}
        snippets={snippets}
        runnerStatus={runnerStatus}
        hostByPrincipal={hostByPrincipal}
        machines={machines}
        assignedByPrincipal={assignedByPrincipal}
        maxAgents={org?.max_agents ?? 3}
        claudeBillingByPrincipal={claudeBillingByPrincipal}
        moduleByPrincipal={moduleByPrincipal}
        effortByPrincipal={effortByPrincipal}
        kpis={kpis}
        runningRunByPrincipal={runningRunByPrincipal}
        canManageOrg={can("manage_org")}
        myPrincipalId={myPrincipal?.id ?? null}
        initialExpandedId={expand ?? null}
      />
    </div>
  );
}
