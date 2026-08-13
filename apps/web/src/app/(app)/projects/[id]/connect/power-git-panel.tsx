"use client";

// US-9.19: Power Git access — per-(principal, project) grants that let a
// developer push through the factory remote WITHOUT claiming a work item.
// Unrestricted by default; the four rails tighten a grant. Writes go straight
// to `git_power_grants` under RLS (manage_project), so a caller without the
// capability sees the state read-only (controls disabled).

import { useState } from "react";
import { GitBranch } from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";

export type PowerMember = {
  principalId: string;
  name: string;
  email: string | null;
  kind: "human" | "agent";
  role: string;
};

export type PowerGrant = {
  allow_default_branch: boolean;
  allow_force_push: boolean;
  allow_branch_delete: boolean;
  allow_tag_push: boolean;
};

type RailKey = keyof PowerGrant;

const RAILS: { key: RailKey; label: (defaultBranch: string) => string }[] = [
  { key: "allow_default_branch", label: (b) => `Push to the default branch (${b})` },
  { key: "allow_force_push", label: () => "Force-push / rewrite history" },
  { key: "allow_branch_delete", label: () => "Delete branches" },
  { key: "allow_tag_push", label: () => "Push tags" },
];

const FULL: PowerGrant = {
  allow_default_branch: true,
  allow_force_push: true,
  allow_branch_delete: true,
  allow_tag_push: true,
};

export function PowerGitPanel({
  projectId,
  orgId,
  defaultBranch,
  members,
  initialGrants,
  canManage,
}: {
  projectId: string;
  orgId: string;
  defaultBranch: string;
  members: PowerMember[];
  initialGrants: Record<string, PowerGrant>;
  canManage: boolean;
}) {
  const supabase = createClient();
  const [grants, setGrants] = useState<Record<string, PowerGrant>>(initialGrants);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function grant(principalId: string) {
    setBusy(principalId);
    setError(null);
    const { error } = await supabase
      .from("git_power_grants")
      .insert({ org_id: orgId, project_id: projectId, principal_id: principalId, ...FULL });
    if (error) setError(error.message);
    else setGrants((g) => ({ ...g, [principalId]: { ...FULL } }));
    setBusy(null);
  }

  async function revoke(principalId: string) {
    setBusy(principalId);
    setError(null);
    const { error } = await supabase
      .from("git_power_grants")
      .delete()
      .eq("project_id", projectId)
      .eq("principal_id", principalId);
    if (error) {
      setError(error.message);
    } else {
      setGrants((g) => {
        const next = { ...g };
        delete next[principalId];
        return next;
      });
    }
    setBusy(null);
  }

  async function setRail(principalId: string, key: RailKey, value: boolean) {
    const prev = grants[principalId];
    if (!prev) return;
    setBusy(principalId);
    setError(null);
    const next = { ...prev, [key]: value };
    const { error } = await supabase
      .from("git_power_grants")
      .update({ [key]: value })
      .eq("project_id", projectId)
      .eq("principal_id", principalId);
    if (error) setError(error.message);
    else setGrants((g) => ({ ...g, [principalId]: next }));
    setBusy(null);
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <GitBranch className="size-4" />
          Power Git access
        </CardTitle>
        <CardDescription>
          Let a member push branches through the factory remote without claiming
          a work item — the branch lands on GitHub, reviewed there. Unrestricted
          by default; uncheck a rail to tighten it.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-2">
        {error && (
          <p className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive">
            {error}
          </p>
        )}
        {members.length === 0 && (
          <p className="text-sm text-muted-foreground">No members to grant.</p>
        )}
        {members.map((m) => {
          const g = grants[m.principalId];
          const on = !!g;
          const initials = (m.name || "?").slice(0, 2).toUpperCase();
          return (
            <div key={m.principalId} className="rounded-lg border p-3">
              <div className="flex items-center gap-3">
                <Avatar className="size-7">
                  <AvatarFallback className="text-[10px]">{initials}</AvatarFallback>
                </Avatar>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="truncate text-sm font-medium">{m.name}</span>
                    <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] uppercase text-muted-foreground">
                      {m.kind}
                    </span>
                  </div>
                  {m.email && (
                    <p className="truncate text-xs text-muted-foreground">{m.email}</p>
                  )}
                </div>
                <label className="flex items-center gap-2 text-xs font-medium">
                  <Checkbox
                    checked={on}
                    disabled={!canManage || busy === m.principalId}
                    onCheckedChange={(v) => (v ? grant(m.principalId) : revoke(m.principalId))}
                    aria-label={`Power Git for ${m.name}`}
                  />
                  Power Git
                </label>
              </div>
              {on && (
                <div className="mt-3 grid gap-2 border-t pt-3 sm:grid-cols-2">
                  {RAILS.map((rail) => (
                    <label
                      key={rail.key}
                      className="flex items-center gap-2 text-xs text-muted-foreground"
                    >
                      <Checkbox
                        checked={g[rail.key]}
                        disabled={!canManage || busy === m.principalId}
                        onCheckedChange={(v) => setRail(m.principalId, rail.key, v)}
                        aria-label={rail.label(defaultBranch)}
                      />
                      {rail.label(defaultBranch)}
                    </label>
                  ))}
                </div>
              )}
            </div>
          );
        })}
        {!canManage && (
          <p className="text-xs text-muted-foreground">
            You need the Manage projects capability to change these.
          </p>
        )}
      </CardContent>
    </Card>
  );
}
