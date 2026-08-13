"use client";

import { useEffect, useState } from "react";
import { KeyRound, Loader2, Pencil, Plus, Trash2 } from "lucide-react";
import { apiFetch } from "@/lib/api";
import { createClient } from "@/lib/supabase/client";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { EmptyState } from "@/components/empty-state";

type BuildConfigRow = { name: string; updated_at: string };

const NAME_RE = /^[A-Za-z_][A-Za-z0-9_]*$/;

/** US-7.9: the write-only build/test config for coding runs. Names are shown;
 * values are never fetched back (they live in the write-only data bucket).
 * Adds/edits/removes flow browser → api → Storage. */
export function BuildConfigCard({ projectId }: { projectId: string }) {
  const [rows, setRows] = useState<BuildConfigRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [newName, setNewName] = useState("");
  const [newValue, setNewValue] = useState("");
  const [busy, setBusy] = useState(false);
  // Editing a value in place (name fixed).
  const [editName, setEditName] = useState<string | null>(null);
  const [editValue, setEditValue] = useState("");

  async function load() {
    const supabase = createClient();
    const { data } = await supabase
      .from("project_build_config")
      .select("name, updated_at")
      .eq("project_id", projectId)
      .order("name", { ascending: true });
    setRows((data ?? []) as BuildConfigRow[]);
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  async function setValue(name: string, value: string) {
    setBusy(true);
    setError(null);
    try {
      await apiFetch(
        `/api/v1/projects/${projectId}/build-config/${encodeURIComponent(name)}`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ value }),
        }
      );
      await load();
      return true;
    } catch (e) {
      setError((e as Error).message);
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function add() {
    const name = newName.trim();
    if (!NAME_RE.test(name)) {
      setError("Name must be a valid env var name (letters, digits, underscore).");
      return;
    }
    if (await setValue(name, newValue)) {
      setNewName("");
      setNewValue("");
    }
  }

  async function remove(name: string) {
    setBusy(true);
    setError(null);
    try {
      await apiFetch(
        `/api/v1/projects/${projectId}/build-config/${encodeURIComponent(name)}`,
        { method: "DELETE" }
      );
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <KeyRound className="size-4 text-muted-foreground" />
          Build configuration
        </CardTitle>
        <CardDescription>
          Write-only key/value config a coding agent needs to build and test —
          a test database URL, sandbox API keys, <span className="font-mono">.env</span>{" "}
          values. Injected into a claimed <strong>code run</strong> of this
          project only. Values are never shown back. This is for{" "}
          <strong>non-production / test / sandbox</strong> use — the one place a
          worker run receives secret values.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-3">
        {rows === null ? (
          <p className="inline-flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="size-3.5 animate-spin" />
            Loading…
          </p>
        ) : rows.length === 0 ? (
          <EmptyState
            icon={KeyRound}
            title="No build config yet"
            description="Add the test/sandbox values an agent needs to build and verify. Many projects need none."
          />
        ) : (
          <ul className="grid gap-1.5">
            {rows.map((r) => (
              <li
                key={r.name}
                className="flex items-center justify-between gap-3 rounded-md border px-3 py-2 text-sm"
              >
                <div className="min-w-0">
                  <span className="font-mono">{r.name}</span>
                  <span className="ml-2 text-xs text-muted-foreground">
                    Set ·{" "}
                    {new Date(r.updated_at).toLocaleDateString(undefined, {
                      month: "short",
                      day: "numeric",
                      year: "numeric",
                    })}
                  </span>
                </div>
                <div className="flex shrink-0 items-center gap-1">
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={busy}
                    onClick={() => {
                      setEditName(r.name);
                      setEditValue("");
                    }}
                  >
                    <Pencil className="size-3.5" />
                    Edit value
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={busy}
                    onClick={() => remove(r.name)}
                  >
                    <Trash2 className="size-3.5" />
                    Remove
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        )}

        {editName && (
          <div className="grid gap-2 rounded-md border p-3">
            <Label htmlFor="bc-edit">
              New value for <span className="font-mono">{editName}</span>
            </Label>
            <div className="flex items-center gap-2">
              <Input
                id="bc-edit"
                type="password"
                autoComplete="new-password"
                value={editValue}
                onChange={(e) => setEditValue(e.target.value)}
              />
              <Button
                size="sm"
                disabled={busy || !editValue}
                onClick={async () => {
                  if (await setValue(editName, editValue)) {
                    setEditName(null);
                    setEditValue("");
                  }
                }}
              >
                Save
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  setEditName(null);
                  setEditValue("");
                }}
              >
                Cancel
              </Button>
            </div>
          </div>
        )}

        <div className="grid gap-2 border-t pt-3 sm:grid-cols-[1fr_1fr_auto] sm:items-end">
          <div className="grid gap-1.5">
            <Label htmlFor="bc-name">Name</Label>
            <Input
              id="bc-name"
              className="font-mono"
              placeholder="TEST_DATABASE_URL"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
            />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="bc-value">Value</Label>
            <Input
              id="bc-value"
              type="password"
              autoComplete="new-password"
              value={newValue}
              onChange={(e) => setNewValue(e.target.value)}
            />
          </div>
          <Button size="sm" disabled={busy || !newName.trim()} onClick={add}>
            {busy ? <Loader2 className="size-4 animate-spin" /> : <Plus className="size-4" />}
            Add
          </Button>
        </div>
        {error && <p className="text-sm font-medium text-destructive">{error}</p>}
      </CardContent>
    </Card>
  );
}
