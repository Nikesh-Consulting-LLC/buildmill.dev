"use client";

// US-32.2: an agent is called `pod-001-1` because that is what provisioning
// generated, and until now nothing in the app could change it. Agents are
// things a manager talks about — "the one that keeps failing on the frontend
// stories" needs a name.
//
// One component, used on the agent's settings page and inline on the fleet
// card, because the fleet card is where a manager notices the name is wrong.
// The write fans out to all three name columns server-side; the machine's
// service name and slot index are untouched.

import { useEffect, useState } from "react";

import { apiCall } from "@/lib/api";

export function AgentRename({
  principalId,
  name,
  compact = false,
  onRenamed,
}: {
  principalId: string;
  name: string;
  /** Inline on a fleet card: no labels, no help text, just the control. */
  compact?: boolean;
  onRenamed?: (name: string) => void | Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(name);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // A rename made elsewhere (or a reload) should not leave a stale draft
  // behind the Rename button.
  useEffect(() => {
    if (!editing) setDraft(name);
  }, [name, editing]);

  async function save() {
    const next = draft.trim();
    if (!next) {
      setError("A name cannot be empty.");
      return;
    }
    if (next === name) {
      setEditing(false);
      setError(null);
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await apiCall(`/api/v1/agents/${principalId}/name`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: next }),
      });
      setEditing(false);
      await onRenamed?.(next);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Rename failed.");
    } finally {
      setSaving(false);
    }
  }

  if (!editing) {
    return (
      <span className="inline-flex items-center gap-2">
        {!compact && <span className="font-medium">{name}</span>}
        <button
          type="button"
          onClick={() => setEditing(true)}
          className="text-xs text-muted-foreground underline underline-offset-4 hover:text-foreground"
        >
          Rename
        </button>
        {error && (
          <span className="text-xs text-red-600 dark:text-red-400">{error}</span>
        )}
      </span>
    );
  }

  return (
    <span className="inline-flex flex-wrap items-center gap-2">
      <input
        value={draft}
        autoFocus
        maxLength={80}
        // US-31.4: a name field is not an identity field — nothing a password
        // manager owns belongs in it.
        autoComplete="off"
        data-1p-ignore="true"
        data-lpignore="true"
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") void save();
          if (e.key === "Escape") {
            setEditing(false);
            setDraft(name);
            setError(null);
          }
        }}
        className="w-48 rounded-md border bg-background px-2 py-1 text-sm"
        aria-label="Agent name"
      />
      <button
        type="button"
        disabled={saving}
        onClick={() => void save()}
        className="rounded-md bg-primary px-2 py-1 text-xs text-primary-foreground disabled:opacity-50"
      >
        {saving ? "Saving…" : "Save"}
      </button>
      <button
        type="button"
        disabled={saving}
        onClick={() => {
          setEditing(false);
          setDraft(name);
          setError(null);
        }}
        className="text-xs text-muted-foreground hover:text-foreground"
      >
        Cancel
      </button>
      {error && (
        <span className="text-xs text-red-600 dark:text-red-400">{error}</span>
      )}
    </span>
  );
}
