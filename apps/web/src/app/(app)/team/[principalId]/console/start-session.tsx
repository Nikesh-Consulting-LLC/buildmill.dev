"use client";

/**
 * US-78.10: open a session with no work item.
 *
 * The CLI window can only ever show a conversation that already exists — a run
 * the agent was dispatched. This is the other way in: pick a project, and the
 * agent opens a session against its checkout with nothing dispatched at all.
 */

import { useState } from "react";
import { MessageSquarePlus } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { apiCall } from "@/lib/api";
import { useRouter } from "@/lib/router-with-progress";

type Project = { id: string; name: string };

export function StartSession({
  workerId,
  projects,
  idleTimeoutMinutes,
}: {
  workerId: string;
  projects: Project[];
  idleTimeoutMinutes: number;
}) {
  const router = useRouter();
  const [projectId, setProjectId] = useState(projects[0]?.id ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function start() {
    if (!projectId) return;
    setBusy(true);
    setError(null);
    try {
      await apiCall("/api/v1/agent-sessions", {
        method: "POST",
        body: JSON.stringify({ worker_id: workerId, project_id: projectId }),
      });
      router.refresh();
    } catch (e) {
      // The API's refusals are sentences on purpose — an agent that is offline,
      // already holding a session, or has no model to reason with. Show them.
      setError(e instanceof Error ? e.message : "The session could not be opened.");
    } finally {
      setBusy(false);
    }
  }

  if (projects.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        This workspace has no project to open a session against.
      </p>
    );
  }

  return (
    <div className="grid gap-2 rounded-md border p-3">
      <p className="text-sm font-medium">Start a session</p>
      <p className="text-xs text-muted-foreground">
        Talk to this agent against a project&apos;s checkout with nothing
        dispatched. It holds the agent while it is open — no runs are claimed —
        and closes itself after {idleTimeoutMinutes} minutes of silence.
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <Select
          items={projects.map((p) => ({ value: p.id, label: p.name }))}
          value={projectId}
          onValueChange={(v) => {
            if (typeof v === "string") setProjectId(v);
          }}
        >
          <SelectTrigger className="w-56">
            <SelectValue placeholder="Pick a project" />
          </SelectTrigger>
          <SelectContent>
            {projects.map((p) => (
              <SelectItem key={p.id} value={p.id}>
                {p.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button size="sm" onClick={start} disabled={busy || !projectId}>
          <MessageSquarePlus className="size-3.5" />
          {busy ? "Opening…" : "Open session"}
        </Button>
      </div>
      {error && (
        <p className="text-xs text-amber-700 dark:text-amber-400">{error}</p>
      )}
    </div>
  );
}
