"use client";

import { useCallback, useEffect, useState } from "react";
import { Loader2, Plus, Trash2, Variable } from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import { apiCall } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { confirmDialog } from "@/components/ui/confirm-dialog";
import { EmptyState } from "@/components/empty-state";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { toastError, toastSuccess } from "@/components/ui/toast";

// US-89.2: one place per project answering "what does an agent get when it
// works here?". Plain entries are ordinary config rows (readable, editable
// under RLS). Secret values are write-only: sent to the API, stored in the
// private bucket, shown only as `Set · <fingerprint>` afterwards.

type EnvRow = {
  id: string;
  agent_id: string | null;
  name: string;
  kind: string;
  value: string | null;
  fingerprint: string | null;
  description: string;
};

type AgentOption = { id: string; name: string };

const NAME_RE = /^[A-Z][A-Z0-9_]*$/;

function errMessage(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

function KindBadge({ kind }: { kind: string }) {
  return (
    <Badge variant={kind === "secret" ? "outline" : "secondary"}>{kind}</Badge>
  );
}

/** Plain entries show their value and edit it in place (RLS allows the
 * update — plain values are config, not credentials). */
function PlainValueEditor({
  row,
  onSaved,
}: {
  row: EnvRow;
  onSaved: () => Promise<void>;
}) {
  const [value, setValue] = useState(row.value ?? "");
  const [saving, setSaving] = useState(false);
  const dirty = value !== (row.value ?? "");

  async function save() {
    setSaving(true);
    try {
      const supabase = createClient();
      const { error } = await supabase
        .from("project_env")
        .update({ value })
        .eq("id", row.id);
      if (error) throw new Error(error.message);
      toastSuccess(`${row.name} updated`);
      await onSaved();
    } catch (e) {
      toastError("Could not update the value", errMessage(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="flex items-center gap-2">
      <Input
        className="font-mono text-xs"
        aria-label={`Value of ${row.name}`}
        value={value}
        onChange={(e) => setValue(e.target.value)}
      />
      {dirty && (
        <Button variant="outline" size="sm" disabled={saving} onClick={save}>
          {saving && <Loader2 className="size-4 animate-spin" />}
          Save
        </Button>
      )}
    </div>
  );
}

/** Secret entries never show their value — only `Set · <fingerprint>` or
 * "Not set", plus a replace box that writes through the API. */
function SecretValueEditor({
  projectId,
  row,
  onSaved,
}: {
  projectId: string;
  row: EnvRow;
  onSaved: () => Promise<void>;
}) {
  const [value, setValue] = useState("");
  const [saving, setSaving] = useState(false);

  async function replace() {
    if (!value.trim()) return;
    setSaving(true);
    try {
      await apiCall(`/api/v1/projects/${projectId}/env/${row.id}/secret`, {
        method: "POST",
        body: JSON.stringify({ value }),
      });
      toastSuccess(`${row.name} secret stored`);
      setValue("");
      await onSaved();
    } catch (e) {
      toastError("Could not store the secret", errMessage(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-2">
      {row.fingerprint ? (
        <Badge variant="secondary" className="font-mono">
          Set · {row.fingerprint}
        </Badge>
      ) : (
        <Badge variant="outline">Not set</Badge>
      )}
      <div className="flex min-w-0 flex-1 items-center gap-2">
        <Input
          type="password"
          autoComplete="off"
          className="font-mono text-xs"
          aria-label={`Replacement value for ${row.name}`}
          placeholder="New value — write-only, never shown back"
          value={value}
          onChange={(e) => setValue(e.target.value)}
        />
        <Button
          variant="outline"
          size="sm"
          disabled={saving || !value.trim()}
          onClick={replace}
        >
          {saving && <Loader2 className="size-4 animate-spin" />}
          Replace value
        </Button>
      </div>
    </div>
  );
}

export function EnvironmentTab({
  projectId,
  orgId,
}: {
  projectId: string;
  orgId: string;
}) {
  const [rows, setRows] = useState<EnvRow[]>([]);
  const [agents, setAgents] = useState<AgentOption[]>([]);
  const [loading, setLoading] = useState(true);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  // Add-entry form.
  const [name, setName] = useState("");
  const [kind, setKind] = useState("plain");
  const [description, setDescription] = useState("");
  const [scope, setScope] = useState("all");
  const [value, setValue] = useState("");
  const [adding, setAdding] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const load = useCallback(async () => {
    const supabase = createClient();
    const [entries, grants] = await Promise.all([
      supabase
        .from("project_env")
        .select("*")
        .eq("project_id", projectId)
        .order("name"),
      supabase
        .from("worker_capabilities")
        .select("worker_id, workers(id, name)")
        .eq("project_id", projectId),
    ]);
    if (entries.error) {
      toastError("Could not load the environment", entries.error.message);
    } else {
      setRows(entries.data as EnvRow[]);
    }
    if (grants.error) {
      toastError("Could not load the project's agents", grants.error.message);
    } else {
      // One worker holds several capability rows on a project — dedupe.
      const seen = new Map<string, string>();
      for (const g of grants.data ?? []) {
        const w = g.workers as unknown as AgentOption | AgentOption[] | null;
        const worker = Array.isArray(w) ? w[0] : w;
        if (worker && !seen.has(worker.id)) seen.set(worker.id, worker.name);
      }
      setAgents(
        [...seen.entries()]
          .map(([id, agentName]) => ({ id, name: agentName }))
          .sort((a, b) => a.name.localeCompare(b.name))
      );
    }
    setLoading(false);
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  const agentNames = new Map(agents.map((a) => [a.id, a.name]));

  async function handleAdd(e: React.FormEvent) {
    e.preventDefault();
    setFormError(null);
    if (!NAME_RE.test(name)) {
      setFormError(
        "Name must be an env-var name: A–Z, digits and underscores, starting with a letter (e.g. DATABASE_URL)."
      );
      return;
    }
    setAdding(true);
    try {
      const supabase = createClient();
      const { data: inserted, error: dbError } = await supabase
        .from("project_env")
        .insert({
          org_id: orgId,
          project_id: projectId,
          agent_id: scope === "all" ? null : scope,
          name,
          kind,
          description: description.trim(),
          value: kind === "plain" ? value : null,
        })
        .select()
        .single();
      if (dbError) {
        setFormError(dbError.message);
        return;
      }
      if (kind === "secret" && value.trim()) {
        // The row exists either way; a failed store just leaves it "Not set"
        // with Replace value as the retry path.
        try {
          await apiCall(
            `/api/v1/projects/${projectId}/env/${inserted.id}/secret`,
            { method: "POST", body: JSON.stringify({ value }) }
          );
          toastSuccess(`${name} added`, "Secret stored — write-only from here.");
        } catch (err) {
          toastError(
            "Entry added, but the secret was not stored",
            `${errMessage(err)} — it shows Not set; use Replace value to retry.`
          );
        }
      } else {
        toastSuccess(`${name} added`);
      }
      setName("");
      setKind("plain");
      setDescription("");
      setScope("all");
      setValue("");
      await load();
    } finally {
      setAdding(false);
    }
  }

  async function handleDelete(row: EnvRow) {
    const ok = await confirmDialog({
      title: `Delete ${row.name}?`,
      description:
        row.kind === "secret"
          ? "The entry and its stored secret value are removed. Agents stop receiving it on their next run."
          : "Agents stop receiving this variable on their next run.",
      confirmLabel: "Delete",
      destructive: true,
    });
    if (!ok) return;
    setDeletingId(row.id);
    try {
      await apiCall(`/api/v1/projects/${projectId}/env/${row.id}`, {
        method: "DELETE",
      });
      toastSuccess(`${row.name} deleted`);
      await load();
    } catch (e) {
      toastError("Could not delete the entry", errMessage(e));
    } finally {
      setDeletingId(null);
    }
  }

  const scopeItems = [
    { value: "all", label: "All agents" },
    ...agents.map((a) => ({ value: a.id, label: a.name })),
  ];

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Environment</CardTitle>
        <CardDescription>
          The variables an agent gets when it works this project — delivered as
          process environment variables at spawn, never written into workspace
          files. A secret&apos;s value is write-only: entered once, shown by
          fingerprint after, never readable back here.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <p className="text-xs text-muted-foreground">
          Always present: the factory MCP server, the factory git remote, the
          LLM gateway — managed by the factory.
        </p>

        {loading ? (
          <div className="flex items-center justify-center p-8">
            <Loader2 className="size-5 animate-spin text-muted-foreground" />
          </div>
        ) : !rows.length ? (
          <EmptyState
            icon={Variable}
            title="No environment entries"
            description="Add the logins, connection strings and flags agents need on this project — they stop guessing the moment these exist."
          />
        ) : (
          <ul className="grid gap-3">
            {rows.map((row) => (
              <li key={row.id} className="rounded-lg border p-4">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <span className="font-mono text-sm font-medium">
                        {row.name}
                      </span>
                      <KindBadge kind={row.kind} />
                      <span className="text-xs text-muted-foreground">
                        {row.agent_id
                          ? (agentNames.get(row.agent_id) ?? "one agent")
                          : "all agents"}
                      </span>
                    </div>
                    {row.description && (
                      <p className="mt-1 text-xs text-muted-foreground">
                        {row.description}
                      </p>
                    )}
                    <div className="mt-2">
                      {row.kind === "secret" ? (
                        <SecretValueEditor
                          projectId={projectId}
                          row={row}
                          onSaved={load}
                        />
                      ) : (
                        <PlainValueEditor row={row} onSaved={load} />
                      )}
                    </div>
                  </div>
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    title={`Delete ${row.name}`}
                    disabled={deletingId === row.id}
                    onClick={() => handleDelete(row)}
                  >
                    {deletingId === row.id ? (
                      <Loader2 className="size-4 animate-spin" />
                    ) : (
                      <Trash2 className="size-4" />
                    )}
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        )}

        <form
          onSubmit={handleAdd}
          className="grid gap-3 rounded-lg border border-dashed p-4"
        >
          <p className="text-sm font-medium">Add entry</p>
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="grid gap-2">
              <Label htmlFor="env-name">Name</Label>
              <Input
                id="env-name"
                className="font-mono"
                placeholder="DATABASE_URL"
                value={name}
                onChange={(e) => setName(e.target.value.toUpperCase())}
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="env-kind">Kind</Label>
              <Select
                items={[
                  { value: "plain", label: "Plain — readable config" },
                  { value: "secret", label: "Secret — write-only" },
                ]}
                value={kind}
                onValueChange={(v) => {
                  if (typeof v === "string") setKind(v);
                }}
              >
                <SelectTrigger id="env-kind" className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="plain">Plain — readable config</SelectItem>
                  <SelectItem value="secret">Secret — write-only</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="grid gap-2">
              <Label htmlFor="env-scope">Scope</Label>
              <Select
                items={scopeItems}
                value={scope}
                onValueChange={(v) => {
                  if (typeof v === "string") setScope(v);
                }}
              >
                <SelectTrigger id="env-scope" className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {scopeItems.map((s) => (
                    <SelectItem key={s.value} value={s.value}>
                      {s.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="grid gap-2">
              <Label htmlFor="env-description">Description</Label>
              <Input
                id="env-description"
                placeholder="What it is — agents read this over MCP"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
              />
            </div>
          </div>
          <div className="grid gap-2">
            <Label htmlFor="env-value">Value</Label>
            <Input
              id="env-value"
              type={kind === "secret" ? "password" : "text"}
              autoComplete="off"
              className="font-mono text-xs"
              placeholder={
                kind === "secret"
                  ? "Stored write-only — never shown back"
                  : "postgres://…"
              }
              value={value}
              onChange={(e) => setValue(e.target.value)}
            />
          </div>
          {formError && (
            <p className="text-sm font-medium text-destructive">{formError}</p>
          )}
          <div>
            <Button type="submit" variant="outline" disabled={adding}>
              {adding ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <Plus className="size-4" />
              )}
              Add entry
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}
