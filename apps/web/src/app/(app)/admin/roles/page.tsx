"use client";

import { confirmDialog } from "@/components/ui/confirm-dialog";

import { useEffect, useMemo, useState } from "react";
import { Loader2, RotateCcw, Save } from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  ROLES,
  ROLE_LABELS,
  CAPABILITIES,
  CAPABILITY_LABELS,
  CAPABILITY_DESCRIPTIONS,
  isLockedCapability,
  type Role,
  type Capability,
} from "@/lib/permissions";

type Grid = Record<string, boolean>;

const key = (role: Role, capability: Capability) => `${role}|${capability}`;

export default function AdminRolesPage() {
  const [grid, setGrid] = useState<Grid | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [savedAt, setSavedAt] = useState<number | null>(null);

  async function load() {
    const supabase = createClient();
    const { data, error: dbError } = await supabase
      .from("role_capabilities")
      .select("role, capability, allowed");
    if (dbError) {
      setError(dbError.message);
      return;
    }
    const next: Grid = {};
    for (const role of ROLES) {
      for (const cap of CAPABILITIES) {
        next[key(role, cap)] = isLockedCapability(role, cap);
      }
    }
    for (const row of data ?? []) {
      next[`${row.role}|${row.capability}`] = row.allowed;
    }
    setGrid(next);
  }

  useEffect(() => {
    load();
  }, []);

  function toggle(role: Role, capability: Capability) {
    if (isLockedCapability(role, capability)) return;
    setGrid((g) => (g ? { ...g, [key(role, capability)]: !g[key(role, capability)] } : g));
    setSavedAt(null);
  }

  const matrix = useMemo(() => {
    if (!grid) return [];
    return ROLES.flatMap((role) =>
      CAPABILITIES.map((capability) => ({
        role,
        capability,
        allowed: grid[key(role, capability)] ?? isLockedCapability(role, capability),
      })),
    );
  }, [grid]);

  async function handleSave() {
    setSaving(true);
    setError(null);
    try {
      await apiFetch("/api/v1/admin/role-capabilities", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ matrix }),
      });
      await load();
      setSavedAt(Date.now());
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  async function handleReset() {
    if (
      !(await confirmDialog({
        title: "Reset capability matrix?",
        description: "Restores the shipped default capabilities for every role.",
        confirmLabel: "Reset",
        destructive: true,
      }))
    )
      return;
    setResetting(true);
    setError(null);
    try {
      await apiFetch("/api/v1/admin/role-capabilities/reset", { method: "POST" });
      await load();
      setSavedAt(Date.now());
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setResetting(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Role capabilities</CardTitle>
        <CardDescription>
          The global default matrix: what each org role can do. Changes take
          effect live for every org on the next request — every gate resolves
          through this grid. &ldquo;View&rdquo; and the owner&apos;s
          &ldquo;Manage org&rdquo; are locked (read access is the floor; an org
          must always have a role that can manage it).
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {!grid ? (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="size-4 animate-spin" /> Loading…
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-sm">
              <thead>
                <tr>
                  <th className="p-2 text-left font-medium">Capability</th>
                  {ROLES.map((role) => (
                    <th key={role} className="p-2 text-center font-medium">
                      {ROLE_LABELS[role]}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {CAPABILITIES.map((cap) => (
                  <tr key={cap} className="border-t">
                    <td className="p-2">
                      <div className="font-medium">{CAPABILITY_LABELS[cap]}</div>
                      <div className="text-xs text-muted-foreground">
                        {CAPABILITY_DESCRIPTIONS[cap]}
                      </div>
                    </td>
                    {ROLES.map((role) => {
                      const locked = isLockedCapability(role, cap);
                      return (
                        <td key={role} className="p-2 text-center">
                          <input
                            type="checkbox"
                            className="size-4 accent-primary disabled:opacity-40"
                            checked={grid[key(role, cap)] ?? locked}
                            disabled={locked}
                            title={locked ? "Locked" : undefined}
                            onChange={() => toggle(role, cap)}
                          />
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <div className="flex items-center gap-2">
          <Button onClick={handleSave} disabled={saving || !grid}>
            {saving ? <Loader2 className="size-4 animate-spin" /> : <Save className="size-4" />}
            Save
          </Button>
          <Button variant="outline" onClick={handleReset} disabled={resetting || !grid}>
            {resetting ? <Loader2 className="size-4 animate-spin" /> : <RotateCcw className="size-4" />}
            Reset to defaults
          </Button>
          {savedAt && <span className="text-xs text-muted-foreground">Saved.</span>}
        </div>

        {error && <p className="text-sm font-medium text-destructive">{error}</p>}
      </CardContent>
    </Card>
  );
}
