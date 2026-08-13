"use client";

// US-57.6: how every agent runs, in one place — model routes, autonomy
// policy, and the three limits the agent settings page used to offer per
// agent (Runs at a time, Minutes per story, Max minutes per run, Attempts
// per work item). One save here reaches every agent's next run immediately
// (migration 204's cascade trigger) — there is no per-agent copy to drift.
//
// US-57.12: `run_routes` (a kind's preset-vs-custom choice) now has a UI for
// its two most-needed fields, effort and turn ceiling — written as a
// `{"custom": {...}}` entry per kind, never `{"preset_id": ...}` (a preset is
// org-scoped; a platform route naming one could only ever resolve inside the
// one org that happens to own that id). A `custom` route replaces a kind's
// resolution wholesale rather than merging onto the org's own preset for it
// (pinned by `test_a_custom_route_stores_its_settings_inline_with_no_preset`
// in `test_run_settings.py`) — leaving effort blank while setting a ceiling
// means that kind stops taking an explicit effort from anywhere. Any
// `run_routes` entry this page doesn't render (a `preset_id` shape, or a kind
// outside `DISPATCH_KINDS`) is preserved on save, never dropped.

import { useEffect, useState } from "react";
import { apiFetch } from "@/lib/api";
import { createClient } from "@/lib/supabase/client";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  DISPATCH_KINDS,
  POLICY_MODES,
} from "../../team/[principalId]/agent-runner-data";

type PlatformLlmKey = {
  model: string;
  key_last4: string | null;
};

type PlatformRunConfig = {
  model_routes: Record<string, string | null>;
  run_routes: Record<string, { custom?: Record<string, unknown>; preset_id?: string } | null>;
  autonomy_policy: {
    mode?: string;
    deny_patterns?: string[];
    allow_patterns?: string[];
  };
  max_run_minutes: number | null;
  max_total_run_minutes: number | null;
  max_item_attempts: number;
  updated_at: string;
};

type AgentModule = { key: string; label: string; available: boolean };

// Mirrors the API's EFFORTS (admin/preset-templates already uses the same
// list) — a platform route's effort is one of these, or unset.
const EFFORTS = ["", "low", "medium", "high", "xhigh", "max"];

function PatternList({
  label,
  patterns,
  onChange,
}: {
  label: string;
  patterns: string[];
  onChange: (next: string[]) => void;
}) {
  const [draft, setDraft] = useState("");
  return (
    <div className="text-sm">
      <span className="mb-1 block text-muted-foreground">{label}</span>
      {patterns.length > 0 && (
        <ul className="mb-2 flex flex-col gap-1">
          {patterns.map((p, idx) => (
            <li key={`${p}-${idx}`} className="flex items-center gap-2">
              <code className="flex-1 truncate rounded border px-2 py-1 text-xs">{p}</code>
              <button
                type="button"
                className="text-xs text-muted-foreground hover:text-foreground"
                onClick={() => onChange(patterns.filter((_, i) => i !== idx))}
              >
                Remove
              </button>
            </li>
          ))}
        </ul>
      )}
      <div className="flex items-center gap-2">
        <Input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="regex, e.g. ^rm -rf"
          className="font-mono text-xs"
        />
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => {
            if (!draft.trim()) return;
            onChange([...patterns, draft.trim()]);
            setDraft("");
          }}
        >
          Add
        </Button>
      </div>
    </div>
  );
}

export default function AdminRunConfigPage() {
  const [config, setConfig] = useState<PlatformRunConfig | null>(null);
  const [modules, setModules] = useState<AgentModule[]>([]);
  const [modelRoutes, setModelRoutes] = useState<Record<string, string>>({});
  // US-57.12: only the effort/turn-ceiling half of `run_routes` — one draft
  // row per dispatch kind. Kinds this page doesn't render (a `preset_id`
  // shape, or a kind outside DISPATCH_KINDS) live only in `rawRunRoutes` and
  // are round-tripped untouched.
  const [runRouteDrafts, setRunRouteDrafts] = useState<
    Record<string, { effort: string; maxTurns: string }>
  >({});
  const [rawRunRoutes, setRawRunRoutes] = useState<PlatformRunConfig["run_routes"]>({});
  const [mode, setMode] = useState("allow");
  const [denyPatterns, setDenyPatterns] = useState<string[]>([]);
  const [allowPatterns, setAllowPatterns] = useState<string[]>([]);
  const [maxRunMinutes, setMaxRunMinutes] = useState("");
  const [maxTotalRunMinutes, setMaxTotalRunMinutes] = useState("");
  const [maxItemAttempts, setMaxItemAttempts] = useState("3");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  // US-60.1: Buildmill Agent's one platform-owned Anthropic key — read
  // directly (RLS lets any authenticated user see `key_last4`/`model`,
  // never the key itself), written via the write-only RPC, same pattern
  // Settings → LLM providers already uses for an org's own keys.
  const [platformKey, setPlatformKey] = useState<PlatformLlmKey | null>(null);
  const [platformKeyDraft, setPlatformKeyDraft] = useState("");
  const [platformKeyBusy, setPlatformKeyBusy] = useState(false);
  const [platformKeyMsg, setPlatformKeyMsg] = useState<string | null>(null);

  async function loadPlatformKey() {
    const supabase = createClient();
    const { data } = await supabase
      .from("platform_llm_key")
      .select("model, key_last4")
      .single();
    setPlatformKey((data as PlatformLlmKey | null) ?? null);
  }

  async function savePlatformKey() {
    if (!platformKeyDraft.trim()) return;
    setPlatformKeyBusy(true);
    setPlatformKeyMsg(null);
    try {
      const supabase = createClient();
      const { error: rpcError } = await supabase.rpc("set_platform_llm_key", {
        p_key: platformKeyDraft.trim(),
      });
      if (rpcError) {
        setPlatformKeyMsg(rpcError.message);
        return;
      }
      setPlatformKeyDraft("");
      await loadPlatformKey();
    } finally {
      setPlatformKeyBusy(false);
    }
  }

  async function clearPlatformKey() {
    setPlatformKeyBusy(true);
    setPlatformKeyMsg(null);
    try {
      const supabase = createClient();
      const { error: rpcError } = await supabase.rpc("clear_platform_llm_key");
      if (rpcError) {
        setPlatformKeyMsg(rpcError.message);
        return;
      }
      await loadPlatformKey();
    } finally {
      setPlatformKeyBusy(false);
    }
  }

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const [cfg, mods] = await Promise.all([
        apiFetch("/api/v1/admin/run-config"),
        apiFetch("/api/v1/admin/modules"),
      ]);
      await loadPlatformKey();
      setConfig(cfg);
      setModules(mods);
      setModelRoutes(cfg.model_routes ?? {});
      const rawRoutes = (cfg.run_routes ?? {}) as PlatformRunConfig["run_routes"];
      setRawRunRoutes(rawRoutes);
      const drafts: Record<string, { effort: string; maxTurns: string }> = {};
      for (const [kind, route] of Object.entries(rawRoutes)) {
        const custom = route?.custom;
        if (!custom) continue;
        drafts[kind] = {
          effort: typeof custom.effort === "string" ? custom.effort : "",
          maxTurns: custom.max_turns != null ? String(custom.max_turns) : "",
        };
      }
      setRunRouteDrafts(drafts);
      setMode(cfg.autonomy_policy?.mode ?? "allow");
      setDenyPatterns(cfg.autonomy_policy?.deny_patterns ?? []);
      setAllowPatterns(cfg.autonomy_policy?.allow_patterns ?? []);
      setMaxRunMinutes(cfg.max_run_minutes != null ? String(cfg.max_run_minutes) : "");
      setMaxTotalRunMinutes(
        cfg.max_total_run_minutes != null ? String(cfg.max_total_run_minutes) : ""
      );
      setMaxItemAttempts(String(cfg.max_item_attempts));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  // US-57.12: reassemble `run_routes` from the effort/turn-ceiling drafts —
  // starting from whatever was loaded so a kind this page doesn't render (a
  // `preset_id` shape) survives untouched, and a kind with both fields blank
  // is omitted entirely rather than saved as `{}`.
  function buildRunRoutes(): Record<string, unknown> {
    const result: Record<string, unknown> = { ...rawRunRoutes };
    for (const k of DISPATCH_KINDS) {
      const existing = rawRunRoutes[k.key];
      if (existing?.preset_id) continue;
      const draft = runRouteDrafts[k.key];
      const custom: Record<string, unknown> = {};
      if (draft?.effort) custom.effort = draft.effort;
      if (draft?.maxTurns.trim()) custom.max_turns = Number(draft.maxTurns);
      if (Object.keys(custom).length > 0) {
        result[k.key] = { custom };
      } else {
        delete result[k.key];
      }
    }
    return result;
  }

  async function save() {
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      await apiFetch("/api/v1/admin/run-config", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          model_routes: modelRoutes,
          run_routes: buildRunRoutes(),
          autonomy_policy: { mode, deny_patterns: denyPatterns, allow_patterns: allowPatterns },
          max_run_minutes: maxRunMinutes.trim() ? Number(maxRunMinutes) : null,
          max_total_run_minutes: maxTotalRunMinutes.trim()
            ? Number(maxTotalRunMinutes)
            : null,
          max_item_attempts: Number(maxItemAttempts),
        }),
      });
      setSaved(true);
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  async function toggleModule(mod: AgentModule) {
    setError(null);
    try {
      await apiFetch(`/api/v1/admin/modules/${mod.key}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ available: !mod.available }),
      });
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  if (loading || !config) {
    return <p className="text-sm text-muted-foreground">Loading…</p>;
  }

  return (
    <div className="flex w-full flex-col gap-8">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">
          How agents run
        </h1>
        <p className="text-sm text-muted-foreground">
          One configuration, inherited by every agent in every org. Saving
          reaches every agent&apos;s next run immediately — there is no
          per-agent copy to fall out of sync.
        </p>
      </div>

      <div>
        <h2 className="mb-2 text-lg font-medium">Agent modules</h2>
        <p className="mb-3 text-sm text-muted-foreground">
          Which coding-agent CLIs an org may choose among. Hiding one gates
          new creation only — an agent already on it keeps running.
        </p>
        <ul className="grid max-w-md gap-2">
          {modules.map((m) => (
            <li
              key={m.key}
              className="flex items-center justify-between rounded-md border px-3 py-2 text-sm"
            >
              <span>
                {m.label}
                <span className="ml-2 font-mono text-xs text-muted-foreground">
                  {m.key}
                </span>
              </span>
              <label className="flex items-center gap-2 text-xs text-muted-foreground">
                Available
                <Checkbox
                  checked={m.available}
                  onCheckedChange={() => void toggleModule(m)}
                />
              </label>
            </li>
          ))}
        </ul>
      </div>

      <div>
        <h2 className="mb-2 text-lg font-medium">Buildmill Agent&apos;s key</h2>
        <p className="mb-3 max-w-lg text-sm text-muted-foreground">
          One Anthropic key, set here, that every org&apos;s Buildmill Agent
          runs bill to instead of bringing their own. Write-only — the key
          itself is never shown again, only a fingerprint.
        </p>
        <div className="flex max-w-lg flex-col gap-2">
          <p className="text-sm">
            {platformKey?.key_last4
              ? `Key set · ends in ····${platformKey.key_last4} · ${platformKey.model}`
              : "No key set — Buildmill Agent runs will fail until one is."}
          </p>
          <div className="flex items-center gap-2">
            <Input
              type="password"
              value={platformKeyDraft}
              onChange={(e) => setPlatformKeyDraft(e.target.value)}
              placeholder="sk-ant-…"
              autoComplete="off"
              className="font-mono text-xs"
            />
            <Button
              type="button"
              variant="outline"
              disabled={platformKeyBusy || !platformKeyDraft.trim()}
              onClick={() => void savePlatformKey()}
            >
              {platformKey?.key_last4 ? "Replace" : "Set key"}
            </Button>
            {platformKey?.key_last4 && (
              <Button
                type="button"
                variant="ghost"
                disabled={platformKeyBusy}
                onClick={() => void clearPlatformKey()}
              >
                Clear
              </Button>
            )}
          </div>
          {platformKeyMsg && (
            <p className="text-sm text-destructive">{platformKeyMsg}</p>
          )}
        </div>
      </div>

      <div>
        <h2 className="mb-2 text-lg font-medium">Model per run kind</h2>
        <p className="mb-3 text-sm text-muted-foreground">
          Left blank, a kind falls back to the module&apos;s own default.
        </p>
        <div className="grid max-w-lg gap-2">
          {DISPATCH_KINDS.map((k) => (
            <div key={k.key} className="grid grid-cols-[8rem_1fr] items-center gap-2">
              <Label className="text-sm">{k.label}</Label>
              <Input
                value={modelRoutes[k.key] ?? ""}
                onChange={(e) =>
                  setModelRoutes((cur) => ({ ...cur, [k.key]: e.target.value }))
                }
                placeholder="inherit the module default"
                className="font-mono text-xs"
              />
            </div>
          ))}
        </div>
      </div>

      <div>
        <h2 className="mb-2 text-lg font-medium">Reasoning effort &amp; turn ceiling per run kind</h2>
        <p className="mb-3 max-w-lg text-sm text-muted-foreground">
          Left blank, a kind inherits the org&apos;s own default preset — including its effort.
          Setting either field here switches that kind fully to these values: it no longer takes
          effort, turn ceiling, or model from any org preset, only from what&apos;s set on this
          row (plus a model still set above, which fills in separately).
        </p>
        <div className="grid max-w-2xl gap-2">
          {DISPATCH_KINDS.map((k) => {
            const isPresetRoute = Boolean(rawRunRoutes[k.key]?.preset_id);
            const draft = runRouteDrafts[k.key] ?? { effort: "", maxTurns: "" };
            return (
              <div
                key={k.key}
                className="grid grid-cols-[8rem_10rem_8rem] items-center gap-2"
              >
                <Label className="text-sm">{k.label}</Label>
                {isPresetRoute ? (
                  <span className="col-span-2 text-xs text-muted-foreground">
                    routed to a preset via the API — not editable here
                  </span>
                ) : (
                  <>
                    <select
                      value={draft.effort}
                      onChange={(e) =>
                        setRunRouteDrafts((cur) => ({
                          ...cur,
                          [k.key]: { ...draft, effort: e.target.value },
                        }))
                      }
                      className="rounded-md border bg-background px-2 py-1 text-sm"
                    >
                      {EFFORTS.map((e) => (
                        <option key={e || "unset"} value={e}>
                          {e || "Inherit"}
                        </option>
                      ))}
                    </select>
                    <Input
                      type="number"
                      min={1}
                      max={500}
                      value={draft.maxTurns}
                      onChange={(e) =>
                        setRunRouteDrafts((cur) => ({
                          ...cur,
                          [k.key]: { ...draft, maxTurns: e.target.value },
                        }))
                      }
                      placeholder="inherit"
                      className="text-sm"
                    />
                  </>
                )}
              </div>
            );
          })}
        </div>
      </div>

      <div>
        <h2 className="mb-2 text-lg font-medium">Autonomy policy</h2>
        <p className="mb-3 text-sm text-muted-foreground">
          What every agent&apos;s shell may do. Deny is checked first, always.
        </p>
        <div className="flex flex-col gap-1.5 text-sm">
          {POLICY_MODES.map((m) => (
            <label key={m.key} className="flex items-start gap-2">
              <input
                type="radio"
                name="platform-mode"
                className="mt-0.5"
                checked={mode === m.key}
                onChange={() => setMode(m.key)}
              />
              <span>
                <span className="font-medium">{m.label}</span>
                <span className="block text-xs text-muted-foreground">{m.help}</span>
              </span>
            </label>
          ))}
        </div>
        <div className="mt-4 grid gap-4 md:grid-cols-2">
          <PatternList
            label="Deny patterns"
            patterns={denyPatterns}
            onChange={setDenyPatterns}
          />
          <PatternList
            label="Allow patterns"
            patterns={allowPatterns}
            onChange={setAllowPatterns}
          />
        </div>
      </div>

      <div>
        <h2 className="mb-2 text-lg font-medium">Limits</h2>
        <div className="grid max-w-sm gap-4">
          <div className="grid gap-1.5">
            <Label htmlFor="max-run-minutes">Minutes per story</Label>
            <Input
              id="max-run-minutes"
              inputMode="numeric"
              value={maxRunMinutes}
              onChange={(e) => setMaxRunMinutes(e.target.value)}
              placeholder="default — 120"
            />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="max-total-run-minutes">Max minutes per run</Label>
            <Input
              id="max-total-run-minutes"
              inputMode="numeric"
              value={maxTotalRunMinutes}
              onChange={(e) => setMaxTotalRunMinutes(e.target.value)}
              placeholder="default — 1440"
            />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="max-item-attempts">Attempts per work item</Label>
            <Input
              id="max-item-attempts"
              inputMode="numeric"
              value={maxItemAttempts}
              onChange={(e) => setMaxItemAttempts(e.target.value)}
            />
          </div>
        </div>
      </div>

      {error && <p className="text-sm text-destructive">{error}</p>}
      <div className="flex items-center gap-3">
        <Button onClick={() => void save()} disabled={saving}>
          {saving ? "Saving…" : "Save"}
        </Button>
        {saved && !saving && (
          <span className="text-sm text-muted-foreground">
            Saved — every agent picks this up on its next run.
          </span>
        )}
      </div>
    </div>
  );
}
