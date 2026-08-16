import Link from "next/link";
import { notFound, redirect } from "next/navigation";
import { ArrowLeft } from "lucide-react";
import { createClient } from "@/lib/supabase/server";
import { factoryMcpUrl, factoryRemoteUrl } from "@/lib/factory-git";
import { loadOrgCapabilities } from "@/lib/permissions";
import { ConnectPanel, type ConnectWorker } from "./connect-panel";
import {
  PowerGitPanel,
  type PowerGrant,
  type PowerMember,
} from "./power-git-panel";

// US-3.10: per-project Connect page — everything a worker needs to work
// on this project, with room to grow (onboarding content, capabilities).
export default async function ProjectConnectPage({
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

  const { data: project } = await supabase
    .from("projects")
    .select("id, org_id, name, slug, default_branch")
    .eq("id", id)
    .maybeSingle();
  if (!project) notFound();

  const { data: org } = await supabase
    .from("organizations")
    .select("shortname")
    .eq("id", project.org_id)
    .maybeSingle();
  const remoteUrl = org?.shortname
    ? factoryRemoteUrl(org.shortname, project.slug)
    : null;
  // us-110.1: one MCP endpoint for the whole org, and no per-worker scope
  // narrowing it. A worker reaches whichever projects its access grants name,
  // so this URL is the same for every worker and the list below is the org's
  // workers, not "this project's" — which is what the query has always
  // returned.
  const mcpUrl = factoryMcpUrl();

  // Workers for the token-in-hand flow.
  const { data: workers } = await supabase
    .from("workers")
    .select("id, name, type, token_last4, status")
    .eq("org_id", project.org_id)
    .order("created_at", { ascending: true });

  // US-9.19: Power Git — org members (principals) + this project's grants.
  const caps = await loadOrgCapabilities(supabase, project.org_id, user.id);
  const canManage = caps.can("manage_project");

  const { data: memberRows } = await supabase
    .from("organization_members")
    .select("role, principal_id, principals(display_name, email, kind)")
    .eq("org_id", project.org_id);

  const members: PowerMember[] = (memberRows ?? [])
    .map((m) => {
      const p = Array.isArray(m.principals) ? m.principals[0] : m.principals;
      return {
        principalId: m.principal_id as string,
        role: (m.role ?? "") as string,
        name: (p?.display_name || p?.email || "Unknown") as string,
        email: (p?.email ?? null) as string | null,
        kind: (p?.kind ?? "human") as "human" | "agent",
      };
    })
    .filter((m) => m.principalId)
    .sort((a, b) => a.name.localeCompare(b.name));

  const { data: grantRows } = await supabase
    .from("git_power_grants")
    .select(
      "principal_id, allow_default_branch, allow_force_push, allow_branch_delete, allow_tag_push",
    )
    .eq("project_id", project.id);

  const initialGrants: Record<string, PowerGrant> = {};
  for (const g of grantRows ?? []) {
    initialGrants[g.principal_id as string] = {
      allow_default_branch: g.allow_default_branch,
      allow_force_push: g.allow_force_push,
      allow_branch_delete: g.allow_branch_delete,
      allow_tag_push: g.allow_tag_push,
    };
  }

  return (
    <div className="flex w-full flex-col gap-6">
      <div>
        <Link
          href={`/projects/${project.id}`}
          className="mb-1 inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="size-3" />
          {project.name}
        </Link>
        <h1 className="text-2xl font-semibold tracking-tight">
          Connect a tool
        </h1>
        <p className="text-sm text-muted-foreground">
          Hook a worker or IDE agent up to {project.name} — pure copy-paste,
          no GitHub credentials.
        </p>
      </div>
      <ConnectPanel
        projectName={project.name}
        mcpUrl={mcpUrl}
        remoteUrl={remoteUrl}
        workers={(workers ?? []) as ConnectWorker[]}
      />
      <PowerGitPanel
        projectId={project.id}
        orgId={project.org_id}
        defaultBranch={project.default_branch ?? "main"}
        members={members}
        initialGrants={initialGrants}
        canManage={canManage}
      />
    </div>
  );
}
