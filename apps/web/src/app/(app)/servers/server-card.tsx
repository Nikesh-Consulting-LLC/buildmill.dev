"use client";

import Link from "next/link";
import { AlertTriangle, Bot, ChevronRight, KeyRound, Server } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { MachineActions } from "./machine-actions";
import { STATUS_LABELS, STATUS_STYLES, probeAge } from "./agent-host";

export type ServerRow = {
  id: string;
  name: string;
  host: string;
  port: number;
  username: string;
  auth_method: "password" | "ssh_key";
  key_fingerprint: string | null;
  host_key_fingerprint: string | null;
  updated_at: string;
};

// US-10.16: which projects (and their deployments) target this machine.
export type ServerUsage = {
  projectId: string;
  projectName: string;
  deployments: string[];
};

/** US-35.2: the agent side of a machine, when it has one. `null` is the normal
 *  state of a deploy target nobody has provisioned to run agents. */
export type MachineAgents = {
  status: string;
  agentCount: number;
  enabledCount: number;
  deadCount: number;
  drifted: boolean;
  lastProbeAt: string | null;
  probeError: string | null;
};

export function ServerCard({
  server,
  orgId,
  usage = [],
  agents = null,
  // US-57.1: /admin/machines reuses this card for platform-owned pools —
  // its detail page lives at a different route than a plain org machine's.
  basePath = "/servers",
}: {
  server: ServerRow;
  orgId: string;
  usage?: ServerUsage[];
  agents?: MachineAgents | null;
  basePath?: string;
}) {
  const probe = agents ? probeAge(agents.lastProbeAt) : null;

  return (
    <Card className="h-full">
      <CardHeader className="flex flex-row items-start justify-between space-y-0">
        <div className="min-w-0">
          <CardTitle className="flex items-center gap-2 text-base">
            <Server className="size-4 text-muted-foreground" />
            {/* US-35.2: the machine has a detail page now — the card is a
                summary of it, not the only place the machine exists. */}
            <Link href={`${basePath}/${server.id}`} className="truncate hover:underline">
              {server.name}
            </Link>
          </CardTitle>
          <p className="mt-1 truncate font-mono text-xs text-muted-foreground">
            {server.username}@{server.host}:{server.port}
          </p>
        </div>
        <Badge variant="secondary" className="shrink-0 gap-1 font-normal">
          <KeyRound className="size-3" />
          {server.auth_method === "password" ? "Password" : "SSH key"}
        </Badge>
      </CardHeader>
      <CardContent className="flex flex-col gap-3 text-sm">
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

        {/* US-35.2: the agent fleet on this machine, where it used to be a
            second card on a second page. Detail stays on the detail page. */}
        {agents && (
          <Link
            href={`/servers/${server.id}`}
            className="flex flex-col gap-1.5 rounded-md border px-2.5 py-2 transition-colors hover:border-ring/60"
          >
            <span className="flex flex-wrap items-center gap-2">
              <Bot className="size-3.5 text-muted-foreground" />
              <Badge className={cn("font-normal", STATUS_STYLES[agents.status])}>
                {STATUS_LABELS[agents.status] ?? agents.status}
              </Badge>
              <span className="text-xs text-muted-foreground">
                {agents.agentCount} {agents.agentCount === 1 ? "agent" : "agents"}
                {` · ${agents.enabledCount} enabled`}
              </span>
              {agents.drifted && (
                <Badge className="bg-amber-100 font-normal text-amber-700 dark:bg-amber-950 dark:text-amber-300">
                  Update available
                </Badge>
              )}
              <ChevronRight className="ml-auto size-3.5 text-muted-foreground" />
            </span>
            {agents.deadCount > 0 && (
              <span className="flex items-center gap-1.5 text-xs text-red-600 dark:text-red-400">
                <AlertTriangle className="size-3.5" />
                {agents.deadCount} enabled{" "}
                {agents.deadCount === 1 ? "agent is" : "agents are"} not running
                on the machine
              </span>
            )}
            {agents.probeError ? (
              <span className="text-xs text-amber-600 dark:text-amber-400">
                Last health check failed: {agents.probeError}
              </span>
            ) : (
              probe && (
                <span
                  className={cn(
                    "text-xs text-muted-foreground",
                    probe.stale && "text-amber-600 dark:text-amber-400"
                  )}
                >
                  {probe.stale
                    ? `last checked ${probe.label} — stale`
                    : `checked ${probe.label}`}
                </span>
              )
            )}
          </Link>
        )}

        {/* US-10.16: which projects (and deployments) target this machine. */}
        <div className="flex flex-col gap-1 border-t pt-2">
          <span className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground/70">
            Used by
          </span>
          {usage.length === 0 ? (
            <p className="text-xs text-muted-foreground">
              No deployments target this machine yet.
            </p>
          ) : (
            <ul className="grid gap-1 text-xs">
              {usage.map((u) => (
                <li key={u.projectId} className="flex flex-wrap items-baseline gap-1.5">
                  <Link
                    href={`/projects/${u.projectId}`}
                    className="font-medium text-foreground hover:underline"
                  >
                    {u.projectName}
                  </Link>
                  <span className="text-muted-foreground">
                    {u.deployments.join(", ")}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>

        <MachineActions orgId={orgId} server={server} />
      </CardContent>
    </Card>
  );
}
