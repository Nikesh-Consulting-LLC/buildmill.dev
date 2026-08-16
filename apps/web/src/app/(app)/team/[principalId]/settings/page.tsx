"use client";

// US-32.1 → US-57.7: an agent's settings, on their own page. Through Phase
// 32/53 this was the form half of the runner console: enabled modules, run
// limits, the model route table, the autonomy policy. US-57.6 made how an
// agent runs (routes, autonomy, the three limits) the platform's one shared
// configuration, cascaded to every agent; this page now offers only what
// stays the org's — the module (from the platform's catalog), billing, and
// which kinds this agent claims.

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";

import { apiCall } from "@/lib/api";
import { createClient } from "@/lib/supabase/client";
import { loadOrgCapabilities } from "@/lib/permissions";
import {
  AGENT_ROLES,
  kindsForRoles,
  rolesArePartial,
  rolesForKinds,
  type AgentRole,
  type AgentRoleKey,
} from "@/lib/agent-roles";
import { AgentRename } from "@/components/agent-rename";
import { RoleIcon } from "@/components/role-icon";
import { cn } from "@/lib/utils";

import { BillingReadiness } from "../../billing-readiness";
import { RemoveMember } from "../../remove-member";
import { AgentTabs } from "../agent-tabs";
import {
  AUTH_LABELS,
  MODULES,
  useAgentRunner,
  type Config,
  type ModuleDeclaration,
  type Session,
  type Worker,
} from "../agent-runner-data";

export default function AgentSettingsPage() {
  const { principalId } = useParams<{ principalId: string }>();
  const router = useRouter();
  const {
    loading,
    name,
    orgId,
    workers,
    otherWorkers,
    slot,
    configs,
    sessions,
    declarations,
    orgModels,
    reload,
  } = useAgentRunner(principalId, { activity: false });

  // us-109.1: removing the agent moved here off the Team row, so this page
  // needs the one capability the roster already had. The database re-checks it
  // under RLS either way — this only decides whether the button is offered.
  const [canManage, setCanManage] = useState(false);
  useEffect(() => {
    if (!orgId) return;
    let cancelled = false;
    (async () => {
      const supabase = createClient();
      const {
        data: { user },
      } = await supabase.auth.getUser();
      if (!user) return;
      const { can } = await loadOrgCapabilities(supabase, orgId, user.id);
      if (!cancelled) setCanManage(can("manage_members"));
    })();
    return () => {
      cancelled = true;
    };
  }, [orgId]);

  if (loading) {
    return <div className="p-1 text-sm text-muted-foreground">Loading agent settings…</div>;
  }

  return (
    <div className="flex w-full flex-col gap-6">
      <div className="flex flex-wrap items-center gap-3">
        <Link href="/team" className="text-sm text-muted-foreground hover:underline">
          ← Team
        </Link>
        <span className="text-muted-foreground">/</span>
        <h1 className="text-xl font-semibold">{name} · settings</h1>
      </div>

      <AgentTabs principalId={principalId} active="settings" />

      {slot && (
        <p className="text-sm text-muted-foreground">
          Runs on{" "}
          <Link
            href={slot.serverId ? `/servers/${slot.serverId}` : "/servers"}
            className="font-medium underline underline-offset-4"
          >
            {slot.hostName}
          </Link>{" "}
          · slot {slot.slotIndex}.
        </p>
      )}

      {/* US-32.2: the name, where a manager is already changing how the agent
          behaves. Renaming moves display_name, workers.name and the slot name
          together; the service name and slot index stay as they are. */}
      {workers.length > 0 && (
        <div className="rounded-lg border p-4 text-sm">
          <span className="mb-1 block text-muted-foreground">Name</span>
          <AgentRename
            principalId={principalId}
            name={name}
            onRenamed={reload}
          />
        </div>
      )}

      {workers.length === 0 ? (
        otherWorkers.length > 0 ? (
          <div className="rounded-md border border-dashed p-8 text-sm text-muted-foreground">
            <p className="font-medium text-foreground">
              This principal is a headless MCP worker — there is nothing to
              configure here.
            </p>
            <p className="mt-2 max-w-2xl">
              It connects per-call over the factory MCP (no persistent socket,
              no shell the factory controls), so runner configuration does not
              apply. What it may claim is set by its{" "}
              <Link href="/team" className="underline underline-offset-4">
                capability grants
              </Link>
              .
            </p>
          </div>
        ) : (
          <div className="rounded-md border border-dashed p-8 text-center text-sm text-muted-foreground">
            This agent has no autonomous runner yet. Mint a token for it on the
            Team page and start one with <code>python -m supervisor</code>.
          </div>
        )
      ) : (
        workers.map((worker) => (
          <div key={worker.id} className="rounded-lg border">
            <div className="flex flex-wrap items-center gap-3 border-b p-4">
              <span className="font-medium">{worker.name}</span>
              <span className="text-xs text-muted-foreground">
                {sessions.some((s) => s.worker_id === worker.id)
                  ? "connected — changes push live"
                  : "offline — changes apply on next connect"}
              </span>
            </div>
            <ConfigEditor
              worker={worker}
              config={configs.find((c) => c.worker_id === worker.id) ?? null}
              session={sessions.find((s) => s.worker_id === worker.id) ?? null}
              online={sessions.some((s) => s.worker_id === worker.id)}
              declarations={declarations}
              orgModels={orgModels}
              onSaved={reload}
            />
          </div>
        ))
      )}

      {/* us-109.1: Remove lives here now, not as an icon button on the Team
          row beside Suspend. It sits outside the per-worker card on purpose —
          an agent with no runner at all is exactly the one a manager comes
          here to delete. */}
      {canManage && orgId && (
        <div className="rounded-lg border border-destructive/30 p-4">
          <h2 className="text-sm font-semibold">Remove this agent</h2>
          <p className="mt-1 max-w-2xl text-xs text-muted-foreground">
            {name} loses access to this org, its tokens are revoked and its
            machine slot is freed for another agent. Work it has already
            merged is unaffected. This cannot be undone — suspend it on the
            Team page instead if it may come back.
          </p>
          <RemoveMember
            orgId={orgId}
            principalId={principalId}
            name={name}
            isAgent
            onRemoved={() => router.push("/team")}
            className="mt-3"
          />
        </div>
      )}
    </div>
  );
}

/** US-77.1: the model pinned for a role, read out of the per-kind map the
 * column still stores. `mixed` is a legacy config whose kinds disagree — one
 * pick has to win on save, so the row says which values are being flattened
 * rather than losing one silently. */
function modelForRole(
  overrides: Record<string, string>,
  role: AgentRole
): { value: string; mixed: boolean; distinct: string[] } {
  const values = role.kinds.map((k) => overrides[k.key] ?? "");
  const distinct = [...new Set(values)];
  return {
    value: values.find(Boolean) ?? "",
    mixed: distinct.length > 1,
    distinct,
  };
}

/** Applying a role's pick to every kind it covers — blank clears them. */
function withRoleModel(
  overrides: Record<string, string>,
  role: AgentRole,
  model: string
): Record<string, string> {
  const next = { ...overrides };
  for (const k of role.kinds) {
    if (model) next[k.key] = model;
    else delete next[k.key];
  }
  return next;
}

// US-57.7: how an agent runs — modules aside, which the platform curates
// (US-57.6) — is the platform's, not this page's. The route/preset table,
// the autonomy policy editor and their supporting components (RouteRow,
// PatternList, badRegex) lived here through Phase 32/53; all removed with
// the fields they edited.

function ConfigEditor({
  worker,
  config,
  session,
  online,
  declarations,
  orgModels,
  onSaved,
}: {
  worker: Worker;
  config: Config | null;
  session: Session | null;
  online: boolean;
  declarations: ModuleDeclaration[];
  orgModels: string[];
  onSaved: () => Promise<void>;
}) {
  // US-66.2: read-only here — set once, in the Add Agent wizard.
  const [modules] = useState<string[]>(
    (config?.enabled_modules as string[] | null) ?? []
  );
  // US-53.1: billing is one agent-level switch, defaulting to metered API.
  const [claudeBilling, setClaudeBilling] = useState<string>(
    ((config as (Config & { claude_billing?: string | null }) | null)
      ?.claude_billing as string | undefined) ?? "api"
  );
  // US-53.4 → US-77.1: one checkbox per ROLE, not per run kind. A never-saved
  // agent (null) means ALL kinds, so the boxes come up all-checked without a
  // migration backfill; a role reads as checked when any of its kinds is
  // stored, and saving writes that role's kinds in full.
  const storedKinds = (
    config as (Config & { enabled_kinds?: string[] | null }) | null
  )?.enabled_kinds;
  const [roles, setRoles] = useState<AgentRoleKey[]>(() =>
    rolesForKinds(storedKinds)
  );
  const enabledKinds = kindsForRoles(roles);
  // True only for a legacy config that holds part of a role — saving widens it
  // to the whole role, which is worth saying before it happens.
  const wideningOnSave = rolesArePartial(storedKinds);
  // US-53.4: an older supervisor ignores the gate; say so instead of being
  // silently believed to obey.
  const runnerHasKindGate = Boolean(
    (
      (session?.host_info as { features?: string[] } | null)?.features ?? []
    ).includes("kind_gate")
  );
  // US-66.1: the org's own per-kind model pin — org-owned, unlike the six
  // platform fields us-57.6 locked down, so it rides in this same PATCH.
  const [modelOverrides, setModelOverrides] = useState<Record<string, string>>(
    () =>
      ((config as (Config & { model_overrides?: Record<string, string> | null }) | null)
        ?.model_overrides as Record<string, string> | undefined) ?? {}
  );
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  // Modules the connected runner reported it actually has (from the hello's
  // host info); unknown when offline or unreported.
  const reported = (session?.host_info as { modules?: string[] } | null)
    ?.modules;

  async function save() {
    setSaving(true);
    setMsg(null);
    try {
      // US-57.6/57.7: model_routes, run_routes, autonomy_policy and the
      // three limits are the platform's now — omitted here entirely, not
      // sent as unchanged, so this PATCH never trips the platform-owned
      // field guard (a body that so much as NAMES one of those six fields
      // is refused for a non-platform-admin, even if the value matches
      // what is already stored).
      const res = await apiCall(`/api/v1/runner/${worker.id}/config`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          // US-66.2: enabled_modules is deliberately absent — locked at
          // creation, not something this form edits any more.
          claude_billing: claudeBilling,
          enabled_kinds: enabledKinds,
          // US-66.1: org-owned, so it travels in the same PATCH as the
          // fields above rather than needing a platform-admin-only path.
          model_overrides: modelOverrides,
        }),
      });
      const changed: string[] = res?.changed ?? [];
      const what = changed.length
        ? `Changed: ${changed.join(", ").replaceAll("_", " ")}.`
        : "Nothing changed.";
      setMsg(
        `${what} ${
          res?.pushed
            ? "Pushed live to the runner."
            : "Runner offline — applies on next connect."
        }`
      );
      await onSaved();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "Save failed.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="p-4">
      <div className="grid gap-6 md:grid-cols-2">
        {/* Module — US-66.2: locked after creation. An agent runs exactly
            one module; letting it change post-creation is how two modules
            ended up enabled on one agent, leaving the runner to guess which
            CLI a run should use. Set only in the Add Agent wizard now. */}
        <div id="modules" className="scroll-mt-24 text-sm">
          <span className="mb-1 block text-muted-foreground">Module</span>
          <p className="mb-2 text-xs text-muted-foreground">
            Set when this agent was created — not editable here. Remove and
            re-create the agent to run a different CLI.
          </p>
          <div className="flex flex-col gap-1.5">
            {modules.length === 0 && (
              <p className="text-xs text-muted-foreground">None set.</p>
            )}
            {MODULES.filter((m) => modules.includes(m.key)).map((m) => {
              const unavailable =
                online && Array.isArray(reported) && !reported.includes(m.key);
              return (
                <div
                  key={m.key}
                  className={`rounded-md border bg-muted/30 px-2.5 py-1.5 ${unavailable ? "opacity-60" : ""}`}
                >
                  <span className="font-medium">{m.label}</span>
                  <span className="block text-xs text-muted-foreground">
                    {m.help}
                    {unavailable &&
                      " Not reported by this machine's last hello — check it's installed."}
                  </span>
                </div>
              );
            })}
          </div>
        </div>

        {/* US-53.1: billing is ONE switch on the agent — never a preset or
            per-kind setting, and never hidden behind a declaration. */}
        {modules.includes("claude") && (
          <div id="billing" className="scroll-mt-24 text-sm">
            <span className="mb-1 block text-muted-foreground">
              Claude billing
            </span>
            <p className="mb-2 text-xs text-muted-foreground">
              How this agent&apos;s Claude runs are paid for. One setting, for
              every run kind.
            </p>
            <select
              value={claudeBilling}
              onChange={(e) => setClaudeBilling(e.target.value)}
              className="w-full max-w-sm rounded-md border bg-background px-2 py-1"
            >
              {Object.entries(AUTH_LABELS).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
            <BillingReadiness
              billing={claudeBilling}
              online={online}
              workerId={worker.id}
              declaresAuth={declarations.some(
                (d) =>
                  d.module === "claude" &&
                  d.settings.some((k) => k.name === "auth"),
              )}
            />
          </div>
        )}
      </div>

      {/* US-53.4 → US-77.1: WHETHER before HOW — four roles, not ten kinds. */}
      <div id="kinds" className="mt-6 scroll-mt-24 text-sm">
        <span className="mb-1 block text-muted-foreground">
          What this agent does
        </span>
        <p className="mb-2 text-xs text-muted-foreground">
          Unchecked roles are never claimed by this agent — the work stays in
          the pool for an agent that does them.
        </p>
        <div className="grid gap-1.5 sm:grid-cols-2">
          {AGENT_ROLES.map((role) => (
            <label
              key={role.key}
              className="flex cursor-pointer items-start gap-2 rounded-md border px-3 py-2 hover:border-ring/60"
            >
              <input
                type="checkbox"
                className="mt-0.5"
                checked={roles.includes(role.key)}
                onChange={(e) =>
                  setRoles(
                    e.target.checked
                      ? [...roles, role.key]
                      : roles.filter((r) => r !== role.key)
                  )
                }
              />
              {/* us-107.3: the same glyph the routing buttons and the team
                  page use, so the checkbox here and the icon on a Dispatch
                  button are visibly the same capability. */}
              <RoleIcon
                role={role.key}
                className={cn(
                  "mt-0.5 shrink-0",
                  roles.includes(role.key)
                    ? "text-foreground"
                    : "text-muted-foreground/40",
                )}
              />
              <span className="min-w-0">
                <span className="block text-xs font-medium">{role.label}</span>
                <span className="block text-xs text-muted-foreground">
                  {role.help}
                </span>
              </span>
            </label>
          ))}
        </div>
        {roles.length === 0 && (
          <p className="mt-1 text-xs text-amber-700 dark:text-amber-400">
            Nothing checked means a benched agent: it will connect and claim no
            work at all.
          </p>
        )}
        {wideningOnSave && (
          <p className="mt-1 text-xs text-muted-foreground">
            This agent was configured before roles existed and holds only part
            of a role. Saving grants the whole role.
          </p>
        )}
        {online && !runnerHasKindGate && (
          <p className="mt-1 text-xs text-amber-700 dark:text-amber-400">
            This runner predates the kind gate and will ignore these roles —
            update it for them to take effect.
          </p>
        )}
      </div>

      {/* US-66.1 → US-77.1: which model THIS agent talks to, per role —
          org-owned, unlike the platform-wide routes/limits/autonomy-policy
          us-57.6 moved to /admin/run-config. Stored per run kind as it always
          was; a role's pick applies to every kind it covers. Blank inherits
          the org's default preset, same as an unrouted kind always has. */}
      <div id="model-overrides" className="mt-6 scroll-mt-24 text-sm">
        <span className="mb-1 block text-muted-foreground">Model per role</span>
        <p className="mb-2 text-xs text-muted-foreground">
          Points just this agent&apos;s runs at a specific model — e.g. a Groq
          model for an OpenCode agent, without changing what any other agent
          in the org falls back to. Blank inherits the org&apos;s default
          preset. Only roles checked above are shown — a role this agent never
          claims has no model to route.
        </p>
        {roles.length === 0 && (
          <p className="mb-2 text-xs text-muted-foreground">
            No roles are checked above yet.
          </p>
        )}
        <div className="grid max-w-md gap-1.5">
          {AGENT_ROLES.filter((role) => roles.includes(role.key)).map((role) => {
            const picked = modelForRole(modelOverrides, role);
            return (
              <div key={role.key} className="grid gap-0.5">
                <div className="grid grid-cols-[8rem_1fr] items-center gap-2">
                  <label htmlFor={`model-override-${role.key}`} className="text-xs">
                    {role.label}
                  </label>
                  <select
                    id={`model-override-${role.key}`}
                    value={picked.value}
                    onChange={(e) =>
                      setModelOverrides((cur) =>
                        withRoleModel(cur, role, e.target.value)
                      )
                    }
                    className="rounded-md border bg-background px-2 py-1 font-mono text-xs"
                  >
                    <option value="">Inherit the org default</option>
                    {orgModels.map((m) => (
                      <option key={m} value={m}>
                        {m}
                      </option>
                    ))}
                    {picked.value && !orgModels.includes(picked.value) && (
                      <option value={picked.value}>
                        {picked.value} (not in org models)
                      </option>
                    )}
                  </select>
                </div>
                {picked.mixed && (
                  <p className="col-start-2 text-xs text-muted-foreground">
                    This role&apos;s kinds were pinned to different models
                    (
                    {picked.distinct.map((m) => m || "inherit").join(", ")}
                    ). Saving applies the one above to all of them.
                  </p>
                )}
              </div>
            );
          })}
        </div>
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-3">
        <button
          onClick={() => void save()}
          disabled={saving}
          className="rounded-md bg-primary px-3 py-1.5 text-sm text-primary-foreground disabled:opacity-50"
        >
          {saving ? "Saving…" : "Save settings"}
        </button>
        {msg && <span className="text-xs text-muted-foreground">{msg}</span>}
      </div>
    </div>
  );
}
