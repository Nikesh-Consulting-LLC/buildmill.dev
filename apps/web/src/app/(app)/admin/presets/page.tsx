"use client";

// US-32.5: run presets — named bundles of how a run should be executed.
//
// Before this, an agent's only tuning surface was a model id per run kind.
// Everything else that decides how a run goes lived in an environment variable
// on the machine, invisible and unmanaged. Nine fields per agent does not scale
// and gives no answer to "how do we do code runs here"; a preset is that answer.
//
// US-56.1: two tabs. Overview carries the outcomes table; Presets is one
// compact row per preset with the nine-field form behind an Edit dialog —
// eleven permanently-open forms was a page nobody could read. The re-seed
// banner stays above the tabs: it is a pending decision, not content.
//
// US-57.16: this page lived at /settings/presets until US-57.7 retired it —
// "presets are platform-authored now (US-57.6)... nothing org-facing replaces
// it." That was true of the org side; nothing superadmin-facing replaced it
// either. /admin/preset-templates only ever covered the four seed templates
// (a model *hint*, no tool grants, no outcomes) — the full nine-field editor,
// with a real model, tool grants, and the per-preset outcomes table, simply
// stopped existing anywhere. Every API route this page calls
// (create/patch/delete/reseed/outcomes) already requires a platform admin
// (`_require_platform_admin_for_preset_write`) and was never removed — only
// the UI to reach them was. This is that UI, unchanged, moved under /admin
// (already superadmin-gated by its own layout) instead of /settings.
//
// Reads the caller's own org membership, same as before — a superadmin who
// is a member of exactly one org (the common case here) manages that org's
// presets. A superadmin managing several orgs from one page is a real gap,
// not solved by this restoration; see the story's Out of scope.

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";

import { apiCall } from "@/lib/api";
import { createClient } from "@/lib/supabase/client";
import { resolveActiveOrg } from "@/lib/active-org";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { toastError, toastSuccess } from "@/components/ui/toast";
import { EmptyState } from "@/components/empty-state";
import { BarChart3, Sparkles } from "lucide-react";

type Preset = {
  id: string;
  template_key: string | null;
  seeded_version: number | null;
  name: string;
  description: string;
  model: string | null;
  settings: Record<string, unknown>;
  version: number;
  // US-34.3: catalog entry ids this preset grants. Empty = the factory server
  // only, which is default deny.
  tool_grants: string[] | null;
};

type ToolServer = { id: string; name: string; enabled: boolean };

type Template = {
  key: string;
  name: string;
  version: number;
  model_hint: string;
};

type ReseedRow = {
  preset_id?: string;
  name: string;
  template_key: string;
  changes: { setting: string; from: unknown; to: unknown }[];
  new?: boolean;
};

// US-33.6: what a preset version actually achieved, from the runs themselves.
type Outcome = {
  name: string;
  version: number;
  runs: number;
  succeeded: number;
  failed: number;
  stopped: number;
  success_rate: number | null;
  cost_usd: number | null;
  avg_cost_usd: number | null;
  avg_seconds: number | null;
};

// US-32.10: the five levels the CLI takes, mirroring `EFFORTS` in the API's
// presets.py — which is what refuses a bad one. A preset is authored with no
// particular machine in view, so unlike the agent settings page this list
// cannot come from a module's declaration.
const EFFORTS = ["", "low", "medium", "high", "xhigh", "max"];

// US-47.1: permission mode is not here. A headless run only reaches its MCP
// tools under `bypassPermissions`, which the runner now sets itself.
const SETTING_HELP: Record<string, string> = {
  effort: "How hard the agent thinks before acting. Higher costs more.",
  max_turns: "A ceiling on agent turns — a run going nowhere stops going there.",
  max_minutes:
    "How long a run of this kind may take. It narrows the agent's own run limit and can never exceed it.",
  fallback_model: "Used when the primary model is overloaded, instead of failing.",
  standing_instructions:
    "Appended to the system prompt for every run on this preset — never replacing it.",
};
// US-53.1: Billing is not here any more. Whose money an agent spends is the
// agent's own switch (Team → agent → Settings), never a preset value.

export default function AdminPresetsPage() {
  const [orgId, setOrgId] = useState<string | null>(null);
  const [presets, setPresets] = useState<Preset[] | null>(null);
  const [templates, setTemplates] = useState<Record<string, Template>>({});
  const [orgModels, setOrgModels] = useState<string[]>([]);
  const [reseed, setReseed] = useState<ReseedRow[] | null>(null);
  const [outcomes, setOutcomes] = useState<Outcome[]>([]);
  const [toolServers, setToolServers] = useState<ToolServer[]>([]);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    const supabase = createClient();
    const {
      data: { user },
    } = await supabase.auth.getUser();
    if (!user) return;
    // US-62.x: this used to pick an arbitrary membership row (no order, no
    // status filter) — for a superadmin who belongs to more than one org
    // (the common case: platform admin + their own test orgs), that could
    // silently resolve to a DIFFERENT org than the one the sidebar switcher
    // shows, and the Edit dialog would write to a stranger's preset with no
    // indication anything was wrong. `resolveActiveOrg` is the one place the
    // rest of the app already agrees on "which org" — matches the sidebar.
    const { orgId: org } = await resolveActiveOrg(supabase, user.id);
    if (!org) return;
    setOrgId(org);

    const [p, t, prov] = await Promise.all([
      supabase
        .from("agent_presets")
        .select(
          "id, template_key, seeded_version, name, description, model, settings, version, tool_grants",
        )
        .eq("org_id", org)
        .is("archived_at", null)
        .order("sort_order", { ascending: true }),
      supabase.from("preset_templates").select("key, name, version, model_hint"),
      supabase.from("llm_providers").select("models, default_model").eq("org_id", org),
    ]);
    // US-34.3: what a preset may grant. Read under RLS; the credential is not in
    // any readable column.
    const tools = await supabase
      .from("mcp_servers")
      .select("id, name, enabled")
      .eq("org_id", org)
      .order("name", { ascending: true });
    setToolServers((tools.data ?? []) as unknown as ToolServer[]);
    setPresets((p.data ?? []) as unknown as Preset[]);
    setTemplates(
      Object.fromEntries(
        ((t.data ?? []) as unknown as Template[]).map((row) => [row.key, row]),
      ),
    );
    const models = new Set<string>();
    for (const row of prov.data ?? []) {
      for (const m of (row.models as string[] | null) ?? []) models.add(m);
      if (row.default_model) models.add(row.default_model as string);
    }
    setOrgModels([...models].sort());

    try {
      const preview = await apiCall(`/api/v1/orgs/${org}/presets/reseed`);
      setReseed([...(preview?.presets ?? []), ...(preview?.missing ?? [])]);
    } catch {
      // A superadmin who cannot see the re-seed offer still gets the presets.
      setReseed([]);
    }
    try {
      // US-33.6: what each preset version actually achieved.
      const res = await apiCall(`/api/v1/orgs/${org}/presets/outcomes?days=90`);
      setOutcomes(res?.outcomes ?? []);
    } catch {
      setOutcomes([]);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function acceptReseed() {
    if (!orgId) return;
    setBusy(true);
    try {
      const res = await apiCall(`/api/v1/orgs/${orgId}/presets/reseed`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      toastSuccess(
        "Presets updated",
        `${(res?.updated ?? []).length} preset(s) now match the platform templates.`,
      );
      await load();
    } catch (e) {
      toastError("Could not update", (e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex w-full flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Presets</h1>
        <p className="max-w-3xl text-sm text-muted-foreground">
          How a run should be executed, as named bundles: model, effort, fallback,
          ceilings, tool grants and standing instructions. An agent&apos;s routes
          pick a preset per run kind, so tuning is choosing a preset rather than
          filling in nine fields correctly. The model must be one{" "}
          <Link
            href="/settings/llm-providers"
            className="underline underline-offset-4"
          >
            your providers
          </Link>{" "}
          actually offer — since the model is what decides which provider
          answers, one that is not listed routes nowhere.
        </p>
      </div>

      {/* US-56.1: above the tabs, deliberately — a pending decision must not
          hide behind the tab the manager happens not to have open. */}
      {reseed && reseed.length > 0 && (
        <div className="rounded-md border border-amber-500/40 bg-amber-500/5 p-4 text-sm">
          <p className="font-medium">
            The platform&apos;s templates have changed since these were seeded.
          </p>
          <ul className="mt-2 space-y-1 text-xs text-muted-foreground">
            {reseed.map((row) => (
              <li key={row.template_key}>
                <span className="font-medium text-foreground">{row.name}</span>
                {row.new ? (
                  <> — not in this org yet; accepting adds it.</>
                ) : row.changes.length === 0 ? (
                  <> — a newer template version with the same effect.</>
                ) : (
                  <>
                    {" — "}
                    {row.changes
                      .map(
                        (c) =>
                          `${c.setting}: ${JSON.stringify(c.from) ?? "unset"} → ${
                            JSON.stringify(c.to) ?? "unset"
                          }`,
                      )
                      .join("; ")}
                  </>
                )}
              </li>
            ))}
          </ul>
          <p className="mt-2 text-xs text-muted-foreground">
            Nothing has changed here yet. Accepting overwrites only the presets
            seeded from a template — anything this org wrote itself is left
            alone.
          </p>
          <Button
            size="sm"
            className="mt-3"
            disabled={busy}
            onClick={() => void acceptReseed()}
          >
            Accept the updates
          </Button>
        </div>
      )}

      <Tabs defaultValue="overview">
        <TabsList>
          <TabsTrigger value="overview">Overview</TabsTrigger>
          <TabsTrigger value="presets">Presets</TabsTrigger>
        </TabsList>

        {/* US-33.6: presets compared on outcome, by name AND version — which
            is why us-32.5 versions them at all. */}
        <TabsContent value="overview">
          {outcomes.length === 0 ? (
            <EmptyState
              icon={BarChart3}
              title="No finished runs yet"
              description="Once runs finish, each preset version's success rate, cost and time show here — grouped by the version they actually ran under, so an edit that made things worse stays visible instead of averaged away."
            />
          ) : (
            <div className="grid gap-2">
              <p className="text-xs text-muted-foreground">
                Finished runs from the last 90 days, grouped by the preset
                version they actually ran under. A version you have since edited
                stays its own row, so a change that made things worse is visible
                instead of averaged away. Cost comes from the metered calls, at
                the rates in force then.
              </p>
              <div className="overflow-x-auto rounded-md border">
                <table className="w-full text-left text-xs">
                  <thead>
                    <tr className="border-b bg-muted/40">
                      <th className="px-3 py-1.5 font-medium">Preset</th>
                      <th className="px-3 py-1.5 text-right font-medium">Runs</th>
                      <th className="px-3 py-1.5 text-right font-medium">
                        Succeeded
                      </th>
                      <th className="px-3 py-1.5 text-right font-medium">
                        Stopped
                      </th>
                      <th className="px-3 py-1.5 text-right font-medium">
                        Avg cost
                      </th>
                      <th className="px-3 py-1.5 text-right font-medium">
                        Avg time
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {outcomes.map((o) => (
                      <tr
                        key={`${o.name}-${o.version}`}
                        className="border-b last:border-0"
                      >
                        <td className="px-3 py-1.5">
                          {o.name}{" "}
                          <span className="text-muted-foreground">
                            v{o.version}
                          </span>
                        </td>
                        <td className="px-3 py-1.5 text-right font-mono">
                          {o.runs}
                        </td>
                        <td className="px-3 py-1.5 text-right font-mono">
                          {o.succeeded}
                          {o.success_rate != null && o.runs >= 5 && (
                            <span className="text-muted-foreground">
                              {" "}
                              ({Math.round(o.success_rate * 100)}%)
                            </span>
                          )}
                        </td>
                        <td className="px-3 py-1.5 text-right font-mono">
                          {o.stopped}
                        </td>
                        <td className="px-3 py-1.5 text-right font-mono">
                          {o.avg_cost_usd != null
                            ? `$${o.avg_cost_usd.toFixed(4)}`
                            : "—"}
                        </td>
                        <td className="px-3 py-1.5 text-right font-mono">
                          {o.avg_seconds != null
                            ? `${Math.round(o.avg_seconds / 60)}m`
                            : "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="text-xs text-muted-foreground">
                A success rate is only shown once a version has five finished
                runs — below that the number would be noise wearing a percentage
                sign.
              </p>
            </div>
          )}
        </TabsContent>

        <TabsContent value="presets">
          {presets === null ? (
            <p className="text-sm text-muted-foreground">Loading presets…</p>
          ) : presets.length === 0 ? (
            <EmptyState
              icon={Sparkles}
              title="No presets"
              description="Every org is seeded with Fast, Balanced, Deep and Investigate. If this org has none, the seed has not run for it — the re-seed offer above will add them."
            />
          ) : (
            <div className="grid gap-4">
              <div className="overflow-x-auto rounded-md border">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Preset</TableHead>
                      <TableHead>Model</TableHead>
                      <TableHead>Effort</TableHead>
                      <TableHead className="text-right">Turns</TableHead>
                      <TableHead className="text-right">Minutes</TableHead>
                      <TableHead className="text-right">Tools</TableHead>
                      <TableHead />
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {presets.map((preset) => (
                      <PresetRow
                        key={preset.id}
                        preset={preset}
                        toolServers={toolServers}
                        template={
                          preset.template_key
                            ? templates[preset.template_key]
                            : undefined
                        }
                        orgModels={orgModels}
                        onSaved={load}
                      />
                    ))}
                  </TableBody>
                </Table>
              </div>
              {orgId && (
                <NewPreset orgId={orgId} orgModels={orgModels} onSaved={load} />
              )}
            </div>
          )}
        </TabsContent>
      </Tabs>
    </div>
  );
}

/** US-56.1: one compact row — the form only exists once Edit is clicked. */
function PresetRow({
  preset,
  template,
  orgModels,
  toolServers,
  onSaved,
}: {
  preset: Preset;
  template?: Template;
  orgModels: string[];
  toolServers: ToolServer[];
  onSaved: () => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const s = (preset.settings ?? {}) as Record<string, unknown>;
  const grants = preset.tool_grants ?? [];

  return (
    <TableRow>
      <TableCell>
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-medium">{preset.name}</span>
          <Badge variant="outline" className="text-[11px]">
            v{preset.version}
          </Badge>
          <Badge variant="secondary" className="font-normal text-[11px]">
            {preset.template_key
              ? `from the ${preset.template_key} template`
              : "written here"}
          </Badge>
        </div>
        {preset.description && (
          <p className="mt-0.5 max-w-md truncate text-xs text-muted-foreground">
            {preset.description}
          </p>
        )}
      </TableCell>
      <TableCell className="font-mono text-xs">
        {preset.model ?? (
          <span className="font-sans text-muted-foreground">inherit</span>
        )}
      </TableCell>
      <TableCell className="text-xs">
        {String(s.effort ?? "") || <span className="text-muted-foreground">—</span>}
      </TableCell>
      <TableCell className="text-right font-mono text-xs">
        {s.max_turns == null ? (
          <span className="font-sans text-muted-foreground">—</span>
        ) : (
          String(s.max_turns)
        )}
      </TableCell>
      <TableCell className="text-right font-mono text-xs">
        {s.max_minutes == null ? (
          <span className="font-sans text-muted-foreground">—</span>
        ) : (
          String(s.max_minutes)
        )}
      </TableCell>
      <TableCell className="text-right font-mono text-xs">
        {grants.length > 0 ? (
          grants.length
        ) : (
          <span className="font-sans text-muted-foreground">—</span>
        )}
      </TableCell>
      <TableCell className="text-right">
        <Button variant="outline" size="sm" onClick={() => setOpen(true)}>
          Edit
        </Button>
        <Dialog open={open} onOpenChange={setOpen}>
          <DialogContent className="max-h-[85vh] gap-0 overflow-y-auto sm:max-w-2xl">
            <DialogHeader>
              <DialogTitle className="flex flex-wrap items-center gap-2">
                {preset.name}
                <Badge variant="outline" className="text-[11px]">
                  v{preset.version}
                </Badge>
              </DialogTitle>
              <DialogDescription>{preset.description}</DialogDescription>
            </DialogHeader>
            {/* Keyed by version so a re-seed or concurrent save never shows
                stale values on the next open. */}
            {open && (
              <PresetForm
                key={`${preset.id}-v${preset.version}`}
                preset={preset}
                template={template}
                orgModels={orgModels}
                toolServers={toolServers}
                onSaved={async () => {
                  await onSaved();
                  setOpen(false);
                }}
                onCancel={() => setOpen(false)}
              />
            )}
          </DialogContent>
        </Dialog>
      </TableCell>
    </TableRow>
  );
}

/** The full nine-field form — unchanged semantics, same PATCH, now mounted
 * only inside the edit dialog (US-56.1). */
function PresetForm({
  preset,
  template,
  orgModels,
  toolServers,
  onSaved,
  onCancel,
}: {
  preset: Preset;
  template?: Template;
  orgModels: string[];
  toolServers: ToolServer[];
  onSaved: () => Promise<void>;
  onCancel: () => void;
}) {
  const s = (preset.settings ?? {}) as Record<string, unknown>;
  const [model, setModel] = useState(preset.model ?? "");
  const [effort, setEffort] = useState(String(s.effort ?? ""));
  const [maxTurns, setMaxTurns] = useState(
    s.max_turns === undefined || s.max_turns === null ? "" : String(s.max_turns),
  );
  const [fallback, setFallback] = useState(String(s.fallback_model ?? ""));
  const [maxMinutes, setMaxMinutes] = useState(
    s.max_minutes === undefined || s.max_minutes === null
      ? ""
      : String(s.max_minutes),
  );
  const [instructions, setInstructions] = useState(
    String(s.standing_instructions ?? ""),
  );
  const [grants, setGrants] = useState<string[]>(preset.tool_grants ?? []);
  const [saving, setSaving] = useState(false);
  const [warnings, setWarnings] = useState<string[]>([]);

  async function save() {
    setSaving(true);
    setWarnings([]);
    try {
      const res = await apiCall(`/api/v1/presets/${preset.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          model: model || null,
          clear_model: !model,
          settings: {
            effort: effort || null,
            max_turns: maxTurns ? Number(maxTurns) : null,
            fallback_model: fallback || null,
            max_minutes: maxMinutes ? Number(maxMinutes) : null,
            standing_instructions: instructions || null,
          },
          tool_grants: grants,
        }),
      });
      setWarnings(res?.warnings ?? []);
      toastSuccess(
        "Saved",
        `${preset.name} is now version ${res?.version ?? preset.version}. Runs already finished keep the version they ran under.`,
      );
      await onSaved();
    } catch (e) {
      toastError("Could not save", (e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="grid gap-4 pt-4 text-sm">
      <div className="grid gap-4 md:grid-cols-2">
        <label className="grid gap-1">
          <span className="text-muted-foreground">Model</span>
          <select
            value={model}
            onChange={(e) => setModel(e.target.value)}
            className="rounded-md border bg-background px-2 py-1"
          >
            <option value="">Inherit the org default</option>
            {orgModels.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
            {model && !orgModels.includes(model) && (
              <option value={model}>{model} (not in org models)</option>
            )}
          </select>
          {template?.model_hint && (
            <span className="text-xs text-muted-foreground">
              Advice from the template: {template.model_hint}
            </span>
          )}
        </label>

        <label className="grid gap-1">
          <span className="text-muted-foreground">Fallback model</span>
          <select
            value={fallback}
            onChange={(e) => setFallback(e.target.value)}
            className="rounded-md border bg-background px-2 py-1"
          >
            <option value="">None</option>
            {orgModels.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
          <span className="text-xs text-muted-foreground">
            {SETTING_HELP.fallback_model}
          </span>
        </label>

        <label className="grid gap-1">
          <span className="text-muted-foreground">Reasoning effort</span>
          <select
            value={effort}
            onChange={(e) => setEffort(e.target.value)}
            className="rounded-md border bg-background px-2 py-1"
          >
            {EFFORTS.map((e) => (
              <option key={e || "unset"} value={e}>
                {e || "Inherit"}
              </option>
            ))}
          </select>
          <span className="text-xs text-muted-foreground">
            {SETTING_HELP.effort}
          </span>
        </label>

        <label className="grid gap-1">
          <span className="text-muted-foreground">Turn ceiling</span>
          <input
            type="number"
            min={1}
            max={500}
            value={maxTurns}
            onChange={(e) => setMaxTurns(e.target.value)}
            placeholder="none"
            className="rounded-md border bg-background px-2 py-1"
          />
          <span className="text-xs text-muted-foreground">
            {SETTING_HELP.max_turns}
          </span>
        </label>

        {/* US-33.2: the one part of time a preset owns. */}
        <label className="grid gap-1">
          <span className="text-muted-foreground">Time ceiling (minutes)</span>
          <input
            type="number"
            min={1}
            max={1440}
            value={maxMinutes}
            onChange={(e) => setMaxMinutes(e.target.value)}
            placeholder="the agent's own limit"
            className="rounded-md border bg-background px-2 py-1"
          />
          <span className="text-xs text-muted-foreground">
            {SETTING_HELP.max_minutes}
          </span>
        </label>
      </div>

      <label className="grid gap-1">
        <span className="text-muted-foreground">Standing instructions</span>
        <textarea
          rows={3}
          maxLength={4000}
          value={instructions}
          onChange={(e) => setInstructions(e.target.value)}
          placeholder="Anything every run on this preset should be told."
          className="rounded-md border bg-background px-2 py-1 font-mono text-xs"
        />
        <span className="text-xs text-muted-foreground">
          {SETTING_HELP.standing_instructions} Up to 4000 characters —{" "}
          {instructions.length} used. An instruction that grows into a second
          prompt competes with the work item for the model&apos;s attention.
        </span>
      </label>

      {/* US-34.3: default deny — a preset with no grants gets the factory
          server and nothing else, which is the state us-31.9 ships. */}
      <div className="grid gap-1">
        <span className="text-muted-foreground">Tool servers</span>
        <p className="text-xs text-muted-foreground">
          What a run on this preset can reach beyond the factory&apos;s own
          tools. Nothing selected means the factory server only. A project can
          still withhold any of these, and a withheld one is reported on the
          run rather than quietly missing.
        </p>
        {toolServers.length === 0 ? (
          <p className="text-xs text-muted-foreground">
            No tool servers registered yet —{" "}
            <Link href="/settings/tools" className="underline underline-offset-4">
              register one
            </Link>
            .
          </p>
        ) : (
          <div className="flex flex-col gap-1">
            {toolServers.map((t) => (
              <label key={t.id} className="flex items-center gap-2 text-xs">
                <input
                  type="checkbox"
                  checked={grants.includes(t.id)}
                  onChange={(e) =>
                    setGrants(
                      e.target.checked
                        ? [...grants, t.id]
                        : grants.filter((g) => g !== t.id),
                    )
                  }
                />
                <span>
                  {t.name}
                  {!t.enabled && (
                    <span className="ml-1 text-amber-700 dark:text-amber-400">
                      (disabled — it will be reported as unavailable)
                    </span>
                  )}
                </span>
              </label>
            ))}
          </div>
        )}
      </div>

      {warnings.length > 0 && (
        <ul className="space-y-1 text-xs text-amber-700 dark:text-amber-400">
          {warnings.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      )}

      <DialogFooter>
        <Button variant="outline" size="sm" disabled={saving} onClick={onCancel}>
          Cancel
        </Button>
        <Button size="sm" disabled={saving} onClick={() => void save()}>
          {saving ? "Saving…" : "Save preset"}
        </Button>
      </DialogFooter>
    </div>
  );
}

function NewPreset({
  orgId,
  orgModels,
  onSaved,
}: {
  orgId: string;
  orgModels: string[];
  onSaved: () => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [model, setModel] = useState("");
  const [effort, setEffort] = useState("");
  const [saving, setSaving] = useState(false);

  if (!open) {
    return (
      <div>
        <Button variant="outline" size="sm" onClick={() => setOpen(true)}>
          New preset
        </Button>
      </div>
    );
  }

  async function create() {
    setSaving(true);
    try {
      await apiCall(`/api/v1/orgs/${orgId}/presets`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name,
          description,
          model: model || null,
          settings: effort ? { effort } : {},
        }),
      });
      toastSuccess("Created", `${name} is available to every agent's routes.`);
      setOpen(false);
      setName("");
      setDescription("");
      setModel("");
      setEffort("");
      await onSaved();
    } catch (e) {
      toastError("Could not create", (e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="grid gap-3 rounded-lg border p-4 text-sm">
      <span className="font-medium">New preset</span>
      <p className="text-xs text-muted-foreground">
        A preset this org wrote itself. A platform re-seed never touches it.
      </p>
      <label className="grid gap-1">
        <span className="text-muted-foreground">Name</span>
        <input
          value={name}
          maxLength={60}
          autoComplete="off"
          data-1p-ignore="true"
          data-lpignore="true"
          onChange={(e) => setName(e.target.value)}
          className="rounded-md border bg-background px-2 py-1"
        />
      </label>
      <label className="grid gap-1">
        <span className="text-muted-foreground">What it is for</span>
        <input
          value={description}
          maxLength={400}
          autoComplete="off"
          onChange={(e) => setDescription(e.target.value)}
          className="rounded-md border bg-background px-2 py-1"
        />
      </label>
      <div className="grid gap-3 md:grid-cols-2">
        <label className="grid gap-1">
          <span className="text-muted-foreground">Model</span>
          <select
            value={model}
            onChange={(e) => setModel(e.target.value)}
            className="rounded-md border bg-background px-2 py-1"
          >
            <option value="">Inherit the org default</option>
            {orgModels.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        </label>
        <label className="grid gap-1">
          <span className="text-muted-foreground">Reasoning effort</span>
          <select
            value={effort}
            onChange={(e) => setEffort(e.target.value)}
            className="rounded-md border bg-background px-2 py-1"
          >
            {EFFORTS.map((e) => (
              <option key={e || "unset"} value={e}>
                {e || "Inherit"}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className="flex gap-2">
        <Button size="sm" disabled={saving || !name.trim()} onClick={() => void create()}>
          {saving ? "Creating…" : "Create"}
        </Button>
        <Button variant="outline" size="sm" onClick={() => setOpen(false)}>
          Cancel
        </Button>
      </div>
    </div>
  );
}
