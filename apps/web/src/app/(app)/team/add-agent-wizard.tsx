"use client";

// US-53.2: adding an agent is one guided flow — who it is, where it runs,
// what it runs, how it bills — instead of a scavenger hunt across Team,
// Machines, the settings page, Presets and LLM providers.
//
// The wizard creates nothing new: every step drives the same calls the
// scattered surfaces make today (`create_worker`, the machine's add-slot job
// with `adopt_worker_id`, the one runner-config PATCH). Nothing is created
// until the final "Create agent" click, so cancelling mid-wizard leaves
// nothing behind — the sequencing the story's cancel criterion asks for.
// Done is a connection, not a summary: the last step waits for the agent's
// first hello and reports the real state, fix named, when it has not come.

import { useEffect, useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import Link from "next/link";
import { Bot, Check, Cpu, Loader2 } from "lucide-react";

import { createClient } from "@/lib/supabase/client";
import { apiCall, type ApiError } from "@/lib/api";
import {
  AGENT_ROLES,
  ALL_ROLE_KEYS,
  kindsForRoles,
  type AgentRoleKey,
} from "@/lib/agent-roles";
import { cn } from "@/lib/utils";
import {
  STATUS_LABELS,
  stateFor,
  statusTextClass,
  type AgentStatus,
} from "@/lib/idle-reasons";
import {
  modulePlaceable,
  resolveActiveModule,
  stepValid as stepIsValid,
  wizardSteps,
} from "@/lib/agent-wizard-steps";
import { RoleIcon } from "@/components/role-icon";
import {
  poolAvailability,
  selectablePools,
  type PoolOption,
} from "@/lib/pool-availability";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { confirmDialog } from "@/components/ui/confirm-dialog";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  buildSnippets,
  pickSnippet,
  TOKEN_PLACEHOLDER,
} from "../settings/worker-connect";
import { CopyBlock } from "./connect-panel";
import { BillingReadiness } from "./billing-readiness";
import {
  AUTH_LABELS,
  MODULES,
  OFFERED_MODULES,
  type ModuleDeclaration,
} from "./[principalId]/agent-runner-data";
import type { MachineOption } from "./team-view";

type Placement = "self" | "machine" | "pool";
type Step = "who" | "where" | "what" | "billing" | "done";

// US-57.3/57.10: the tenant's only window onto a shared machine — name,
// coarse status and free count, never a host. `PoolOption` and the three-way
// availability decision live in @/lib/pool-availability so they can be tested
// without rendering a five-step form.

/** The runner env-block, from the single snippet source (the us-3.7 "no
 * drift" rule). The runner snippet embeds neither URL, so placeholders are
 * safe here. */
function runnerBlock(token: string): string {
  return pickSnippet(
    buildSnippets({ mcpUrl: "", gitCloneUrl: "" }),
    "runner",
  ).text.replaceAll(TOKEN_PLACEHOLDER, token);
}

/** What the creation sequence has produced so far — the Done step's subject,
 * and, on a mid-sequence failure, exactly what the cancel criterion makes the
 * wizard name. */
type Created = {
  workerId: string;
  principalId: string | null;
  token: string;
  /** The machine add-slot job, machine placement only. */
  jobId: string | null;
  /** US-57.3 follow-on: the pool's host was busy with another job, so
   * placement was accepted but has no job yet — `pool_placement_sweep`
   * runs it once the host frees up. */
  poolQueued?: boolean;
};

export function AddAgentWizard({
  orgId,
  machines,
  onChanged,
  // US-57.2: the org's agent quota, so the button explains a block instead
  // of opening a wizard that create_worker will refuse at the last step.
  quota,
}: {
  orgId: string;
  machines: MachineOption[];
  onChanged: () => void;
  quota?: { used: number; max: number };
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [step, setStep] = useState<Step>("who");

  // Step 1 — who it is, and what it does.
  const [name, setName] = useState("");
  // US-77.1: four roles, all on by default. `enabled_kinds` still stores run
  // kinds — the roles expand on save. us-111.1: asked on step 1 beside the
  // name, because what an agent is for shapes everything asked after it.
  const [roles, setRoles] = useState<AgentRoleKey[]>([...ALL_ROLE_KEYS]);

  // Step 2 — what it runs, and where.
  // US-66.2: an agent runs exactly one module — two enabled at once left the
  // runner guessing which CLI a run should use.
  // us-111.1: the type moved here from step 3, above the placement it
  // constrains (a pool-only type has no owned-machine option), and Buildmill
  // Interactive Agent is the default. It is pool-only, so `activeModule` falls
  // back to the first type that has somewhere to run when the org has no pool
  // with room — the default must never be a dead end.
  const [moduleKey, setModuleKey] = useState("interactive");
  const [placement, setPlacement] = useState<Placement | null>(null);
  const [machineId, setMachineId] = useState("");
  const [pools, setPools] = useState<PoolOption[]>([]);
  const [poolId, setPoolId] = useState("");

  // Step 3 — which projects.
  // US-55.1: which projects it may access — all checked by default, because
  // the gate is fail-closed and an agent with zero access rows claims
  // nothing. The wizard used to create exactly that agent.
  // us-110.1: this list is the whole answer. A second, single-select "which
  // project its MCP tools connect to" used to sit under it writing
  // workers.project_id — a narrower scope that silently stopped an agent
  // claiming runs in projects checked here. The column is gone; what is
  // checked is what the agent works on.
  const [accessIds, setAccessIds] = useState<string[]>([]);

  // Step 4 — how it bills (claude only).
  const [billing, setBilling] = useState("api");

  // Org data the What step needs; fetched when the wizard opens.
  const [projects, setProjects] = useState<{ id: string; name: string }[]>([]);
  // US-57.6: the superadmin's catalog — a module absent here (hidden) is
  // never offered, regardless of what the machine's own probe reported.
  const [availableModuleKeys, setAvailableModuleKeys] = useState<
    Set<string> | null
  >(null);

  // Creation state.
  const [creating, setCreating] = useState(false);
  const [created, setCreated] = useState<Created | null>(null);
  /** A mid-sequence failure: the identity exists but a later call failed.
   *  Named honestly, with removal offered — never a silent half-agent. */
  const [partial, setPartial] = useState<{
    what: string;
    error: string;
  } | null>(null);
  const [removing, setRemoving] = useState(false);

  const machine = machines.find((m) => m.hostId === machineId) ?? null;

  // US-77.2: the agent types on offer — the two the factory creates today,
  // minus anything the superadmin hid (us-57.6). A `null` catalog means still
  // loading, which offers the full set rather than an empty form.
  const offeredModules = OFFERED_MODULES.filter(
    (m) => availableModuleKeys === null || availableModuleKeys.has(m.key),
  );
  /** us-111.1: a pool-only type has nowhere to run when the org has no pool
   *  with room. Interactive is the default now, so this is the difference
   *  between opening on a sensible choice and opening on a dead Next button. */
  const hasSelectablePool = selectablePools(pools).length > 0;
  const placeable = (m: { poolOnly?: boolean }) =>
    modulePlaceable(m, hasSelectablePool);
  /** The selection, corrected for a catalog that does not contain it — and
   *  (us-111.1) for a type that has no placement available. Derived rather
   *  than pushed into state by an effect, so the form can never render a
   *  moment where nothing is selected and Next is refused for a reason the
   *  manager cannot see. Empty only when the catalog offers nothing at all. */
  const activeModule = resolveActiveModule(
    moduleKey,
    offeredModules,
    hasSelectablePool,
  );
  /** US-78.6: the Buildmill Interactive Agent runs on a platform pool only —
   *  it holds a live session the platform provisions, patches and can reach.
   *  Derived from the same MODULES table the radios render from, so the rule
   *  has one statement here and is separately enforced by the API and a
   *  database trigger. */
  const poolOnly =
    MODULES.find((m) => m.key === activeModule)?.poolOnly === true;

  // US-78.6 → us-111.1: the type and the placement are chosen on the same step
  // now, so switching to a pool-only type retracts the machine option in the
  // same interaction rather than a page later. The snap-back stays anyway: the
  // manager can still pick a machine first and change the type second, on the
  // one page, and Next must not proceed with a placement no longer rendered.
  useEffect(() => {
    if (poolOnly && placement === "machine") {
      setPlacement(hasSelectablePool ? "pool" : null);
      setMachineId("");
    }
  }, [poolOnly, placement, hasSelectablePool]);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    (async () => {
      const supabase = createClient();
      const [projRows, poolRows, moduleRows] = await Promise.all([
        supabase
          .from("projects")
          .select("id, name")
          .eq("org_id", orgId)
          .order("name", { ascending: true }),
        // US-57.3: the tenant's one window onto shared machines — name and
        // free count only, already ordered fullest-free first.
        supabase.rpc("available_agent_pools"),
        // US-57.6: the platform's module catalog — a hidden module never
        // appears here, regardless of what a machine's probe reported.
        supabase.from("agent_modules").select("key, available"),
      ]);
      if (cancelled) return;
      const projectList = (projRows.data ?? []) as { id: string; name: string }[];
      setProjects(projectList);
      setAccessIds(projectList.map((p) => p.id));

      const poolOptions: PoolOption[] = (
        (poolRows.data ?? []) as {
          pool_id: string;
          pool_name: string;
          status: string;
          free_slots: number;
        }[]
      ).map((p) => ({
        poolId: p.pool_id,
        poolName: p.pool_name,
        status: p.status,
        freeSlots: p.free_slots,
      }));
      setPools(poolOptions);
      const available = new Set(
        ((moduleRows.data ?? []) as { key: string; available: boolean }[])
          .filter((m) => m.available)
          .map((m) => m.key),
      );
      setAvailableModuleKeys(available);
      setModuleKey((cur) => {
        if (available.has(cur)) return cur;
        const first = MODULES.find((m) => available.has(m.key));
        return first?.key ?? cur;
      });
      // The system picks an available pool automatically; the user may only
      // ever override to one that is ready AND has room (us-57.10 widened what
      // is reported, not what may be chosen).
      const withRoom = selectablePools(poolOptions);
      if (withRoom.length > 0) {
        setPoolId((cur) => cur || withRoom[0].poolId);
        setPlacement((cur) => cur ?? "pool");
        setBilling((cur) => (cur === "api" ? "subscription" : cur));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open, orgId]);

  function reset() {
    setStep("who");
    setName("");
    setPlacement(null);
    setMachineId("");
    setPoolId("");
    // us-111.1 (was US-66.2's OpenCode): mirrors the initial useState above,
    // which reset() had drifted from once already.
    setModuleKey("interactive");
    setRoles([...ALL_ROLE_KEYS]);
    setAccessIds(projects.map((p) => p.id));
    setBilling("api");
    setCreated(null);
    setPartial(null);
  }

  // US-61.2: the wizard no longer offers a preset/model choice — every
  // agent it creates inherits the org default, exactly what an empty
  // run_routes already means. Per-kind tuning still lives on the settings
  // page, unchanged.
  function buildRunRoutes(): Record<string, unknown> {
    return {};
  }

  /** The creation sequence, all at the end so cancel is clean before it:
   *  1. `create_worker` — the identity (a principal like any team member).
   *  2. the project-access rows (US-55.1) — one 'access' row per checked
   *     project, or the fail-closed gate leaves the new agent claiming
   *     nothing.
   *  3. the one runner-config PATCH — modules, kinds, billing, preset route.
   *  4. machine placement only: the add-slot job with `adopt_worker_id`, the
   *     same endpoint the machine page's button posts.
   */
  async function create(confirmCapacity = false) {
    setCreating(true);
    setPartial(null);
    const supabase = createClient();
    try {
      // 1 — identity. Same call the retired dialog made.
      let who = created;
      if (!who) {
        const { data, error: rpcError } = await supabase.rpc("create_worker", {
          p_org: orgId,
          p_name: name.trim(),
          p_type: "autonomous",
        });
        if (rpcError) {
          setPartial({ what: "", error: rpcError.message });
          return;
        }
        const row = Array.isArray(data) ? data[0] : data;
        if (!row?.token) {
          setCreating(false);
          setPartial({
            what: "",
            error: "Agent created but no token returned — regenerate it.",
          });
          return;
        }
        const { data: w } = await supabase
          .from("workers")
          .select("principal_id")
          .eq("id", row.worker_id)
          .maybeSingle();
        // US-61.1: create_worker assigns the fixed 'agent' role itself —
        // there is nothing left here to apply.
        who = {
          workerId: row.worker_id as string,
          principalId: (w?.principal_id as string | null) ?? null,
          token: row.token as string,
          jobId: null,
        };
        setCreated(who);
      }

      // 2 — project access, plain CRUD like the Team page's toggles. Upsert
      // (ignore duplicates) so a retry after a later step's failure re-runs
      // cleanly through rows that already landed.
      if (accessIds.length > 0) {
        const workerId = who.workerId;
        const { error: accessError } = await supabase
          .from("worker_capabilities")
          .upsert(
            accessIds.map((project_id) => ({
              org_id: orgId,
              worker_id: workerId,
              project_id,
              capability: "access",
            })),
            {
              onConflict: "worker_id,project_id,capability",
              ignoreDuplicates: true,
            },
          );
        if (accessError) {
          setPartial({
            what: `The agent identity “${name.trim()}” was created, but granting it project access failed`,
            error: accessError.message,
          });
          return;
        }
      }

      // 3 — the config, one PATCH. The same body shape the settings page
      // saves, so everything the wizard sets is editable there afterwards.
      try {
        await apiCall(`/api/v1/runner/${who.workerId}/config`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            enabled_modules: [activeModule],
            enabled_kinds: kindsForRoles(roles),
            run_routes: buildRunRoutes(),
            // US-77.2: neither branch fires for an agent created today —
            // claude and buildmill are no longer offered — but they are the
            // correct bodies if either is re-offered, which is one flag in
            // MODULES.
            ...(activeModule === "claude"
              ? { claude_billing: billing }
              : // US-60.1: Buildmill Agent has nothing to choose — the API
                // forces this regardless, but naming it here keeps the
                // intent visible in the one place this body is built.
                activeModule === "buildmill"
                ? { claude_billing: "platform" }
                : {}),
          }),
        });
      } catch (e) {
        setPartial({
          what: `The agent identity “${name.trim()}” was created, but configuring it failed`,
          error: (e as Error).message,
        });
        return;
      }

      // 4 — machine placement: the same add-slot job the machine page posts,
      // bound to the identity just created so the flow stays one agent.
      if (placement === "machine" && machineId && !who.jobId) {
        try {
          const res = await apiCall(`/api/v1/agent-servers/${machineId}/slots`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              slots: 1,
              adopt_worker_id: who.workerId,
              confirm_capacity: confirmCapacity,
            }),
          });
          who = { ...who, jobId: (res?.job_id as string | null) ?? null };
          setCreated(who);
        } catch (e) {
          const err = e as ApiError;
          const detail = err.detail as
            | { confirmable?: boolean; message?: string }
            | undefined;
          // US-26.7's advisory capacity check: name the numbers, let the
          // operator confirm through — a hard block would overrule them on
          // their own hardware.
          if (err.status === 409 && detail?.confirmable) {
            setCreating(false);
            if (
              await confirmDialog({
                title: "Add the agent anyway?",
                description:
                  detail.message ?? "This machine is at its capacity.",
                confirmLabel: "Add agent",
              })
            )
              return create(true);
            setPartial({
              what: `The agent identity “${name.trim()}” was created and configured, but it has no machine yet`,
              error: detail.message ?? "The machine is at capacity.",
            });
            return;
          }
          setPartial({
            what: `The agent identity “${name.trim()}” was created and configured, but installing it on the machine failed`,
            error: (e as Error).message,
          });
          return;
        }
      }

      // 4 (pool variant) — the tenant-facing placement endpoint (us-57.3):
      // no host to name, no confirmable override — a full pool is a hard
      // refusal, since capacity there is the superadmin's own decision.
      if (placement === "pool" && poolId && !who.jobId && !who.poolQueued) {
        try {
          const res = await apiCall(`/api/v1/agent-pools/${poolId}/place`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ worker_id: who.workerId }),
          });
          // US-57.3 follow-on: a busy host answers 202 "queued" rather than
          // a 409 — the placement is accepted, not failed, so the wizard
          // proceeds to Done exactly as the immediate-placement path does.
          who = {
            ...who,
            jobId: (res?.job_id as string | null) ?? null,
            poolQueued: res?.queued === true,
          };
          setCreated(who);
        } catch (e) {
          setPartial({
            what: `The agent identity “${name.trim()}” was created and configured, but placing it on the pool failed`,
            error: (e as Error).message,
          });
          return;
        }
      }

      onChanged();
      setStep("done");
    } finally {
      setCreating(false);
    }
  }

  /** Undo a partial creation with the roster's own retirement primitive —
   *  deleting the org membership revokes the tokens with it. */
  async function removePartial() {
    if (!created?.principalId) return;
    setRemoving(true);
    try {
      const supabase = createClient();
      const { error } = await supabase
        .from("organization_members")
        .delete()
        .eq("org_id", orgId)
        .eq("principal_id", created.principalId);
      if (error) {
        setPartial({ what: partial?.what ?? "", error: error.message });
        return;
      }
      onChanged();
      setCreated(null);
      setPartial(null);
    } finally {
      setRemoving(false);
    }
  }

  // The visible step sequence — billing renders only when the module is
  // claude (whose money a run spends is a Claude-only concept today).
  // Built inline rather than memoized: four literals cost nothing, and the
  // React Compiler refuses to optimize a component whose useMemo depends on a
  // derived value like `activeModule` (us-77.2).
  const steps: { id: Step; label: string }[] = wizardSteps(activeModule);
  const stepIndex = steps.findIndex((s) => s.id === step);
  const lastInputStep = steps[steps.length - 2].id;

  // us-111.1: the type gates Where now (it is chosen there, and it decides
  // which placements exist); Projects is unconditional — every project is
  // checked by default and an unchecked-all agent is warned about, not blocked.
  const stepValid = stepIsValid(step, {
    name,
    activeModule,
    placement,
    machineId,
    poolId,
  });

  function next() {
    if (steps[stepIndex + 1]) setStep(steps[stepIndex + 1].id);
  }
  function back() {
    if (stepIndex > 0) setStep(steps[stepIndex - 1].id);
  }

  // Modules the chosen machine's probe reported. Empty means it never said —
  // no opinion, not "supports nothing" (the same reading the settings page
  // gives an undeclared machine).
  const machineModules =
    placement === "machine" && machine && machine.modules.length > 0
      ? machine.modules
      : null;

  /** Picking a machine that never installed the chosen module must not leave
   *  a checked-but-disabled radio on the What step — snap to the first module
   *  the machine did report. */
  function chooseMachine(hostId: string) {
    setMachineId(hostId);
    const m = machines.find((x) => x.hostId === hostId);
    if (m && m.modules.length > 0 && !m.modules.includes(activeModule)) {
      // US-77.2: from what the wizard OFFERS, not the whole registry — a
      // machine reporting only `claude` must not snap the form to an agent
      // type that is no longer a choice.
      const first = OFFERED_MODULES.find((mod) => m.modules.includes(mod.key));
      if (first) setModuleKey(first.key);
    }
  }

  const quotaFull = !!quota && quota.used >= quota.max;
  if (quotaFull) {
    return (
      <Button variant="outline" disabled title={`This org has reached its agent limit (${quota!.used} of ${quota!.max}). Ask the superadmin to raise it.`}>
        <Bot className="size-4" />
        {quota!.used} of {quota!.max} agents
      </Button>
    );
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        // No closing mid-creation: the sequence would finish with nobody
        // watching, which is exactly the half-created state the story bans.
        if (!o && creating) return;
        setOpen(o);
        if (!o) reset();
      }}
    >
      <DialogTrigger render={<Button variant="create" />}>
        <Bot className="size-4" />
        Add agent
        {/* US-91.13: the count is the only thing on this button carrying
            information, and `text-muted-foreground` is a neutral picked for a
            page background — on the `create` variant's saturated fill it was
            close to unreadable. De-emphasise with the button's OWN foreground
            instead of borrowing another surface's. */}
        {quota && (
          <span className="text-xs text-create-foreground/75">
            ({quota.used} of {quota.max})
          </span>
        )}
      </DialogTrigger>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Add an agent</DialogTitle>
          <DialogDescription>
            One guided flow — who it is, where it runs, what it runs, how it
            bills. Nothing is created until the final step.
          </DialogDescription>
        </DialogHeader>

        {/* Step rail */}
        <ol className="flex flex-wrap items-center gap-1 text-xs">
          {steps.map((s, i) => (
            <li key={s.id} className="flex items-center gap-1">
              {i > 0 && <span className="text-muted-foreground">·</span>}
              <span
                className={cn(
                  "rounded-full px-2 py-0.5",
                  s.id === step
                    ? "bg-primary text-primary-foreground"
                    : i < stepIndex
                      ? "text-foreground"
                      : "text-muted-foreground",
                )}
              >
                {i + 1} {s.label}
              </span>
            </li>
          ))}
        </ol>

        {partial ? (
          <div className="grid gap-3">
            {partial.what ? (
              <>
                <p className="text-sm font-medium">{partial.what}.</p>
                <p className="text-sm text-destructive">{partial.error}</p>
                <p className="text-xs text-muted-foreground">
                  Remove it to start over cleanly, or keep it and finish on its
                  settings page — everything the wizard sets is editable there.
                </p>
                <div className="flex flex-wrap items-center gap-2">
                  {created?.principalId ? (
                    <Button
                      variant="outline"
                      disabled={removing}
                      onClick={() => void removePartial()}
                    >
                      {removing && <Loader2 className="size-4 animate-spin" />}
                      Remove it
                    </Button>
                  ) : (
                    <p className="text-xs text-muted-foreground">
                      Its principal could not be read back — remove it from the
                      Team roster.
                    </p>
                  )}
                  {created?.principalId && (
                    <Button
                      variant="outline"
                      onClick={() => {
                        const pid = created.principalId;
                        setOpen(false);
                        reset();
                        router.push(`/team/${pid}/settings`);
                      }}
                    >
                      Keep it — open its settings
                    </Button>
                  )}
                  <Button variant="ghost" onClick={() => setPartial(null)}>
                    Try again
                  </Button>
                </div>
              </>
            ) : (
              <>
                {/* Creation failed before anything existed — plain error. */}
                <p className="text-sm text-destructive">{partial.error}</p>
                <Button variant="ghost" onClick={() => setPartial(null)}>
                  Back
                </Button>
              </>
            )}
          </div>
        ) : (
          <>
            {step === "who" && (
              <div className="grid gap-4">
                <div className="grid gap-2">
                  <Label htmlFor="wizard-name">Name</Label>
                  <Input
                    id="wizard-name"
                    placeholder="Runner (Claude Code)"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                  />
                  <p className="text-xs text-muted-foreground">
                    The identity is a principal like any team member — it
                    shows on the roster and can be assigned work.
                  </p>
                </div>

              {/* US-61.2 → US-77.1: four roles, not ten pipeline kinds. The
                  model each role talks to is tuned on the agent's settings
                  page afterwards. */}
              <div className="grid gap-1.5 text-sm">
                <span className="text-muted-foreground">
                  What this agent does
                </span>
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
                              : roles.filter((r) => r !== role.key),
                          )
                        }
                      />
                      {/* us-107.3: the same glyph the roster and the
                          routing buttons use, so a capability is picked
                          here wearing the icon it will wear everywhere. */}
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
                        <span className="block text-xs font-medium">
                          {role.label}
                        </span>
                        <span className="block text-xs text-muted-foreground">
                          {role.help}
                        </span>
                      </span>
                    </label>
                  ))}
                </div>
                <p className="text-xs text-muted-foreground">
                  Unchecked roles are never claimed by this agent — the work
                  stays in the pool for one that does them.
                </p>
                {roles.length === 0 && (
                  <p className="text-xs text-amber-700 dark:text-amber-400">
                    Nothing checked means a benched agent: it will connect
                    and claim no work at all.
                  </p>
                )}
              </div>
              </div>
            )}

            {step === "where" && (
              <div className="grid gap-3">
                {/* us-111.1: the type sits above the placement it
                    constrains. A pool-only type retracts the
                    owned-machine option below in the same interaction,
                    rather than un-choosing it a page later. */}
              <div className="grid gap-1.5 text-sm">
                <span className="text-muted-foreground">Agent Type</span>
                {/* US-66.2 → US-77.2: one module per agent, not a checklist
                    — two enabled modules left the runner guessing which CLI
                    a run should use. Radios show every option and the one
                    chosen at once, which a closed dropdown cannot. */}
                {offeredModules.length === 0 ? (
                  <p className="text-xs text-amber-700 dark:text-amber-400">
                    No agent type is available — the superadmin has hidden
                    every one. Ask for one to be enabled before creating an
                    agent.
                  </p>
                ) : (
                  <div className="grid gap-1.5 sm:grid-cols-2">
                    {offeredModules.map((m) => {
                      // US-53.2: only modules the chosen machine can
                      // actually run — the probe's word, checked where the
                      // choice is made, not at first dispatch.
                      // us-111.1: and a pool-only type with no pool that has
                      // room. Disabled and said out loud here, rather than
                      // selectable-then-stuck at the placement below it.
                      const noPlacement = !placeable(m);
                      const unavailable =
                        noPlacement ||
                        (machineModules !== null &&
                          !machineModules.includes(m.key));
                      return (
                        <label
                          key={m.key}
                          className={cn(
                            "flex items-start gap-2 rounded-md border px-3 py-2",
                            unavailable
                              ? "cursor-not-allowed opacity-60"
                              : "cursor-pointer hover:border-ring/60",
                            activeModule === m.key && "border-ring bg-muted/40",
                          )}
                        >
                          <input
                            type="radio"
                            name="wizard-module"
                            className="mt-0.5"
                            value={m.key}
                            checked={activeModule === m.key}
                            disabled={unavailable}
                            onChange={() => setModuleKey(m.key)}
                          />
                          <span className="min-w-0">
                            <span className="block text-xs font-medium">
                              {m.label}
                            </span>
                            <span className="block text-xs text-muted-foreground">
                              {noPlacement
                                ? "Runs on a Build Mill pool only, and no pool has room — free a slot or resize one to pick this."
                                : unavailable
                                  ? `Not installed on ${machine?.name ?? "the machine"} — its probe did not report it.`
                                  : m.help}
                            </span>
                          </span>
                        </label>
                      );
                    })}
                  </div>
                )}
                {placement === "self" && (
                  <p className="text-xs text-muted-foreground">
                    Install the matching CLI on the machine you run it on —
                    the runner drives it.
                  </p>
                )}
              </div>
                {(() => {
                  // US-57.10: an empty list used to mean three different
                  // things and got one sentence — which told a manager to
                  // resize a pool that had 31 of 32 slots free and was simply
                  // broken. `poolAvailability` says which of the three it is.
                  const availability = poolAvailability(pools);
                  const withRoom = selectablePools(pools);
                  const chosenPool = pools.find((p) => p.poolId === poolId) ?? null;
                  return (
                    <button
                      type="button"
                      disabled={withRoom.length === 0}
                      onClick={() => {
                        setPlacement("pool");
                        if (!poolId && withRoom[0]) setPoolId(withRoom[0].poolId);
                      }}
                      className={cn(
                        "flex items-start gap-3 rounded-md border px-3 py-3 text-left text-sm hover:border-ring/60",
                        placement === "pool" && "border-ring",
                        withRoom.length === 0 && "cursor-not-allowed opacity-50",
                      )}
                    >
                      <Bot className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
                      <span className="grid w-full gap-1">
                        <span className="font-medium">An agent pool</span>
                        <span className="text-xs text-muted-foreground">
                          The platform runs it for you — bring your own Claude
                          license. No machine to set up or manage.
                        </span>
                        {availability.state !== "available" ? (
                          <span className="text-xs text-amber-700 dark:text-amber-400">
                            {availability.message}
                          </span>
                        ) : placement === "pool" && chosenPool ? (
                          <span
                            className="grid gap-1 pt-1"
                            onClick={(e) => e.stopPropagation()}
                          >
                            {withRoom.length > 1 ? (
                              <Select
                                items={withRoom.map((p) => ({
                                  value: p.poolId,
                                  label: `${p.poolName} · ${p.freeSlots} free`,
                                }))}
                                value={poolId}
                                onValueChange={(v) => {
                                  if (typeof v === "string") setPoolId(v);
                                }}
                              >
                                <SelectTrigger className="h-8 w-full text-xs">
                                  <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                  {withRoom.map((p) => (
                                    <SelectItem key={p.poolId} value={p.poolId}>
                                      {p.poolName} · {p.freeSlots} free
                                    </SelectItem>
                                  ))}
                                </SelectContent>
                              </Select>
                            ) : (
                              <span className="text-xs font-medium text-foreground">
                                {chosenPool.poolName} · {chosenPool.freeSlots} free
                              </span>
                            )}
                          </span>
                        ) : null}
                      </span>
                    </button>
                  );
                })()}
                {/* US-57.3: registering a NEW org-owned host retired with
                    /servers's dialog — this option only appears for an org
                    that already has one (grandfathered), never as a way to
                    get one. */}
                {/* US-78.6: an interactive agent is not offered this branch at
                    all. It holds a live session on hardware the platform
                    provisions and can reach, so a machine the platform does not
                    control is not a placement it has. Said out loud below
                    rather than by an option quietly vanishing. */}
                {poolOnly && machines.length > 0 && (
                  <p className="rounded-md border border-dashed px-3 py-2 text-xs text-muted-foreground">
                    A {MODULES.find((m) => m.key === activeModule)?.label} runs
                    on a Build Mill pool only — not on a machine you manage.
                  </p>
                )}
                {!poolOnly && machines.length > 0 && (
                  <>
                    <button
                      type="button"
                      onClick={() => setPlacement("machine")}
                      className={cn(
                        "flex items-start gap-3 rounded-md border px-3 py-3 text-left text-sm hover:border-ring/60",
                        placement === "machine" && "border-ring",
                      )}
                    >
                      <Cpu className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
                      <span className="grid gap-0.5">
                        <span className="font-medium">On a machine I manage</span>
                        <span className="text-xs text-muted-foreground">
                          An org-owned machine, registered before this became a
                          platform pool.
                        </span>
                      </span>
                    </button>
                    {placement === "machine" && (
                      <div className="grid gap-2 pl-7">
                        <Label htmlFor="wizard-machine">Machine</Label>
                        <Select
                          items={machines.map((m) => ({
                            value: m.hostId,
                            label: `${m.name} · ${m.agentCount} agent${m.agentCount === 1 ? "" : "s"}`,
                          }))}
                          value={machineId}
                          onValueChange={(v) => {
                            if (typeof v === "string") chooseMachine(v);
                          }}
                        >
                          <SelectTrigger id="wizard-machine" className="w-full">
                            <SelectValue placeholder="Pick a machine" />
                          </SelectTrigger>
                          <SelectContent>
                            {machines.map((m) => (
                              <SelectItem key={m.hostId} value={m.hostId}>
                                {`${m.name} · ${m.agentCount} agent${m.agentCount === 1 ? "" : "s"}`}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                        <p className="text-xs text-muted-foreground">
                          The wizard provisions one slot on it when you create
                          the agent. Capacity is checked then — a tight
                          machine warns and lets you confirm through.
                        </p>
                      </div>
                    )}
                  </>
                )}
                {/* "My own machine" hidden from the UI per request
                    2026-08-07 — the placement itself ("self") is untouched
                    plumbing (create(), DoneStep) so nothing breaks if it's
                    ever re-shown; only this entry point is gone. */}
                {placement === "self" && (
                  <div className="grid gap-2 pl-7">
                    <CopyBlock text={runnerBlock(TOKEN_PLACEHOLDER)} />
                    <p className="text-xs text-muted-foreground">
                      The real token is minted when the agent is created — the
                      final step shows this block again with it filled in.
                    </p>
                  </div>
                )}
              </div>
            )}

            {step === "what" && (
              <div className="grid gap-4">

                {/* US-55.1: project access, all checked by default — the gate
                    is fail-closed, so an agent created with none can claim
                    nothing at all. us-110.1: and this is now the ONLY project
                    question. The single-select MCP scope that used to sit
                    above it is gone, so what is checked here is what the
                    agent works on — over git, over MCP, and in the pool. */}
                <div className="grid gap-1.5 text-sm">
                  <span className="text-muted-foreground">
                    Which projects it works on
                  </span>
                  {projects.length === 0 ? (
                    <p className="text-xs text-muted-foreground">
                      No projects yet — grant access from the agent&apos;s
                      Team page once one exists.
                    </p>
                  ) : (
                    <div className="flex flex-wrap gap-x-4 gap-y-1.5">
                      {projects.map((p) => (
                        <label
                          key={p.id}
                          className="flex items-center gap-1.5 text-xs"
                        >
                          <input
                            type="checkbox"
                            checked={accessIds.includes(p.id)}
                            onChange={(e) =>
                              setAccessIds(
                                e.target.checked
                                  ? [...accessIds, p.id]
                                  : accessIds.filter((id) => id !== p.id),
                              )
                            }
                          />
                          {p.name}
                        </label>
                      ))}
                    </div>
                  )}
                  <p className="text-xs text-muted-foreground">
                    On every project checked, the agent does whatever the
                    roles above allow — it claims that project&apos;s runs and
                    reaches its code over git and MCP.
                  </p>
                  {projects.length > 0 && accessIds.length === 0 && (
                    <p className="text-xs text-amber-700 dark:text-amber-400">
                      No project checked — the agent can claim nothing until
                      access is granted on its Team page.
                    </p>
                  )}
                </div>
              </div>
            )}

            {step === "billing" && (
              <div className="grid gap-2 text-sm">
                <span className="text-muted-foreground">Claude billing</span>
                <p className="text-xs text-muted-foreground">
                  How this agent&apos;s Claude runs are paid for. One setting,
                  for every run kind — editable later on its settings page.
                </p>
                {placement === "pool" && billing === "subscription" && (
                  <p className="text-xs text-amber-700 dark:text-amber-400">
                    Connecting a Claude subscription for a pool-placed agent
                    is not wired up yet — this agent will show as not
                    connected until that lands. Pick API billing to run it
                    today.
                  </p>
                )}
                <select
                  value={billing}
                  onChange={(e) => setBilling(e.target.value)}
                  className="w-full max-w-sm rounded-md border bg-background px-2 py-1.5"
                >
                  {Object.entries(AUTH_LABELS).map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </select>
                {/* US-53.1's readiness checks, inline and pre-creation:
                    warns and links fixes, never blocks. */}
                <BillingReadiness
                  billing={billing}
                  online={false}
                  declaresAuth={false}
                  workerId={null}
                  machineConnectedAt={
                    placement === "machine"
                      ? (machine?.claudeConnectedAt ?? null)
                      : null
                  }
                  pending
                />
              </div>
            )}

            {step === "done" && created && (
              <DoneStep
                created={created}
                placement={placement ?? "self"}
                machine={machine}
                moduleKey={activeModule}
                billing={billing}
                onChanged={onChanged}
                onClose={() => {
                  setOpen(false);
                  reset();
                }}
                onOpenProfile={(pid) => {
                  setOpen(false);
                  reset();
                  router.push(`/team/${pid}`);
                }}
              />
            )}

            {step !== "done" && (
              <div className="flex items-center gap-2 pt-2">
                {stepIndex > 0 && (
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    disabled={creating}
                    onClick={back}
                  >
                    Back
                  </Button>
                )}
                {step === lastInputStep ? (
                  <Button
                    className="flex-1"
                    disabled={creating || !stepValid}
                    onClick={() => void create()}
                  >
                    {creating && <Loader2 className="size-4 animate-spin" />}
                    Create agent
                  </Button>
                ) : (
                  <Button
                    className="flex-1"
                    disabled={!stepValid}
                    onClick={next}
                  >
                    Next
                  </Button>
                )}
              </div>
            )}
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------- Done step
/**
 * Done is a connection, not a summary: poll for the agent's first hello
 * (runner_sessions, every 3s) and report the live result — connected with
 * modules and billing readiness, or an honest "not connected yet" with the
 * fix named. "Created but never connected" is a state, not a failure screen.
 */
function DoneStep({
  created,
  placement,
  machine,
  moduleKey,
  billing,
  onChanged,
  onClose,
  onOpenProfile,
}: {
  created: Created;
  placement: Placement;
  machine: MachineOption | null;
  moduleKey: string;
  billing: string;
  onChanged: () => void;
  onClose: () => void;
  onOpenProfile: (principalId: string) => void;
}) {
  const [session, setSession] = useState<{
    host_info: unknown;
    module_settings: unknown;
  } | null>(null);
  const [jobStatus, setJobStatus] = useState<string | null>(null);
  const [slot, setSlot] = useState<{
    id: string;
    desired_state: string;
  } | null>(null);
  const [enabling, setEnabling] = useState(false);
  const [enableError, setEnableError] = useState<string | null>(null);
  // us-116.4: the new agent's state — the same word the roster will show it
  // with, from the same status the roster reads.
  const [status, setStatus] = useState<AgentStatus | null>(null);

  useEffect(() => {
    let cancelled = false;
    const supabase = createClient();
    async function poll() {
      // us-116.4: presence is the `live_runner_sessions` view — connected AND
      // heartbeated inside the window — the one predicate every surface reads.
      const s = await supabase
        .from("live_runner_sessions")
        .select("host_info, module_settings")
        .eq("worker_id", created.workerId)
        .order("connected_at", { ascending: false })
        .limit(1)
        .maybeSingle();
      if (!cancelled)
        setSession(
          (s.data as { host_info: unknown; module_settings: unknown } | null) ??
            null,
        );
      try {
        const st = await apiCall(`/api/v1/runner/${created.workerId}/idle-reason`);
        if (!cancelled) setStatus((st as AgentStatus) ?? null);
      } catch {
        // the state line is context; the connection poll above still answers
      }
      // US-57.3 follow-on: a queued pool placement has no job at all yet —
      // `pool_placement_sweep` creates one once the host frees up — so the
      // slot poll must not wait on a job id existing.
      if (created.jobId || (placement === "pool" && created.poolQueued)) {
        // US-57.4: on a shared pool the job row is the platform's — RLS
        // hides it from the tenant entirely, so a pool placement reads only
        // the slot (which the tenant's own org can see) and never the job.
        const [j, sl] = await Promise.all([
          placement === "pool"
            ? Promise.resolve({ data: null })
            : supabase
                .from("agent_server_jobs")
                .select("status")
                .eq("id", created.jobId)
                .maybeSingle(),
          supabase
            .from("agent_slots")
            .select("id, desired_state")
            .eq("worker_id", created.workerId)
            .eq("status", "active")
            .limit(1)
            .maybeSingle(),
        ]);
        if (!cancelled) {
          setJobStatus((j.data?.status as string | null) ?? null);
          setSlot(
            (sl.data as { id: string; desired_state: string } | null) ?? null,
          );
        }
      }
    }
    void poll();
    const t = setInterval(() => void poll(), 3000);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [created.workerId, created.jobId, created.poolQueued, placement]);

  const online = session !== null;
  const reportedModules =
    ((session?.host_info as { modules?: string[] } | null)?.modules ?? null) ||
    null;
  const declaresAuth = (
    (session?.module_settings as ModuleDeclaration[] | null) ?? []
  ).some(
    (d) => d.module === "claude" && d.settings.some((k) => k.name === "auth"),
  );
  const moduleMissing =
    online && Array.isArray(reportedModules) && !reportedModules.includes(moduleKey);

  // US-26.5: machine slots come up paused so a fresh install claims nothing
  // by accident. The wizard's whole promise is "can take work when green",
  // so enabling is offered right here — one explicit click. A pool slot uses
  // the tenant-scoped endpoint (US-57.4) — the host-scoped one 403s a
  // tenant, since it authorizes on the platform org, not the slot's own.
  async function enable() {
    if (!slot || !created.principalId) return;
    setEnabling(true);
    setEnableError(null);
    try {
      // us-116.5: Start means start — the agent's own endpoint (authorized on
      // the slot's org, so a pool tenant can use it), which also restarts the
      // service if the agent is not live.
      await apiCall(`/api/v1/agents/${created.principalId}/start`, { method: "POST" });
      setSlot({ ...slot, desired_state: "enabled" });
      onChanged();
    } catch (e) {
      setEnableError((e as Error).message);
    } finally {
      setEnabling(false);
    }
  }

  return (
    <div className="grid gap-3 text-sm">
      {placement === "self" && (
        <>
          <p className="text-muted-foreground">
            The agent exists and its token is minted. Run this on your machine
            — the token can be read again any time from Team → Connect:
          </p>
          <CopyBlock text={runnerBlock(created.token)} />
        </>
      )}

      {online ? (
        <div className="grid gap-1.5">
          <p className="flex items-center gap-1.5 font-medium text-emerald-600 dark:text-emerald-400">
            <Check className="size-4" /> Connected — the runner said hello.
          </p>
          {/* us-116.4: what the roster will call it, from the same status. */}
          {status && (
            <p className="text-xs" data-testid="agent-state">
              <span className={cn("font-medium", statusTextClass(stateFor(online, status)))}>
                {STATUS_LABELS[stateFor(online, status)]}
              </span>
              {status.detail ? (
                <span className="text-muted-foreground"> — {status.detail}</span>
              ) : null}
            </p>
          )}
          {Array.isArray(reportedModules) && (
            <p className="text-xs text-muted-foreground">
              Modules on the machine: {reportedModules.join(", ") || "none"}
            </p>
          )}
          {moduleMissing && (
            <p className="text-xs text-amber-700 dark:text-amber-400">
              It has not reported {moduleKey} — install that CLI on the
              machine, then restart the runner.
            </p>
          )}
          {moduleKey === "claude" && (
            <BillingReadiness
              billing={billing}
              online={online}
              declaresAuth={declaresAuth}
              workerId={created.workerId}
            />
          )}
          {(placement === "machine" || placement === "pool") &&
            slot &&
            (slot.desired_state === "enabled" ? (
              <p className="text-xs text-muted-foreground">
                Enabled — it can claim work now.
              </p>
            ) : (
              <div className="grid gap-1">
                <p className="text-xs text-muted-foreground">
                  It starts paused, so a fresh install claims nothing by
                  accident. Enable it to start claiming:
                </p>
                <Button
                  variant="outline"
                  className="w-fit"
                  disabled={enabling}
                  onClick={() => void enable()}
                >
                  {enabling && <Loader2 className="size-4 animate-spin" />}
                  Enable the agent
                </Button>
                {enableError && (
                  <p className="text-xs text-destructive">{enableError}</p>
                )}
              </div>
            ))}
        </div>
      ) : (
        <div className="grid gap-1.5">
          <p className="flex items-center gap-1.5 text-muted-foreground">
            <Loader2 className="size-4 animate-spin" />
            {placement === "self"
              ? "Not connected yet — start the runner with the command above."
              : placement === "pool"
                ? // US-57.4: no job status to read here at all — the tenant
                  // can see only its own slot, never the platform's job row.
                  created.poolQueued
                    ? "The machine is finishing another job first — this will start automatically."
                    : "Setting up your agent…"
                : jobStatus === "failed" || jobStatus === "partial"
                  ? "The install job did not finish cleanly."
                  : jobStatus === "succeeded"
                    ? "Installed — waiting for the runner's first hello."
                    : `Installing on ${machine?.name ?? "the machine"}…`}
          </p>
          {placement === "machine" && (
            <p className="text-xs text-muted-foreground">
              {jobStatus === "failed" || jobStatus === "partial" ? (
                <>
                  Open{" "}
                  <Link
                    href={
                      machine?.serverId
                        ? `/servers/${machine.serverId}`
                        : "/servers"
                    }
                    className="underline underline-offset-4"
                  >
                    the machine page
                  </Link>{" "}
                  for the job log. The agent identity exists on the roster —
                  remove it there if you abandon this install.
                </>
              ) : (
                <>
                  The machine is setting up the agent service over SSH — this
                  takes a minute. Progress also shows on{" "}
                  <Link
                    href={
                      machine?.serverId
                        ? `/servers/${machine.serverId}`
                        : "/servers"
                    }
                    className="underline underline-offset-4"
                  >
                    the machine page
                  </Link>
                  .
                </>
              )}
            </p>
          )}
          {placement === "pool" && (
            <p className="text-xs text-muted-foreground">
              This can take a minute. The agent identity exists on the roster
              — remove it there if you abandon this.
            </p>
          )}
          <p className="text-xs text-muted-foreground">
            You can close this — the agent exists, and its settings page shows
            the same connection state.
          </p>
        </div>
      )}

      <div className="flex items-center gap-2 pt-1">
        {created.principalId && (
          <Button
            variant="outline"
            onClick={() => onOpenProfile(created.principalId!)}
          >
            Open its profile
          </Button>
        )}
        <Button variant="ghost" onClick={onClose}>
          Close
        </Button>
      </div>
    </div>
  );
}
