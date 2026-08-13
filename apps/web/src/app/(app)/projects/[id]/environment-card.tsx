"use client";

// us-5.23: the project's declared environment — runtime/version, ordered
// setup commands, notes. Shown beside run commands on the Guidelines tab
// so the manager sees what workers will be told; workers read the same
// values as a structured `environment` object in the work context.

import { useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import { Loader2, Wrench } from "lucide-react";
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
import { Textarea } from "@/components/ui/textarea";

export type ProjectEnvironment = {
  env_runtime: string;
  env_setup_commands: string[];
  env_notes: string;
};

export function EnvironmentCard({
  projectId,
  environment,
}: {
  projectId: string;
  environment: ProjectEnvironment;
}) {
  const router = useRouter();
  const [runtime, setRuntime] = useState(environment.env_runtime);
  const [setup, setSetup] = useState(environment.env_setup_commands.join("\n"));
  const [notes, setNotes] = useState(environment.env_notes);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const dirty =
    runtime !== environment.env_runtime ||
    setup !== environment.env_setup_commands.join("\n") ||
    notes !== environment.env_notes;

  async function save() {
    setSaving(true);
    setError(null);
    const supabase = createClient();
    const { error: updateError } = await supabase
      .from("projects")
      .update({
        env_runtime: runtime.trim(),
        env_setup_commands: setup
          .split("\n")
          .map((l) => l.trim())
          .filter(Boolean),
        env_notes: notes.trim(),
      })
      .eq("id", projectId);
    setSaving(false);
    if (updateError) {
      setError(updateError.message);
      return;
    }
    router.refresh();
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <Wrench className="size-4 text-muted-foreground" />
          Environment
        </CardTitle>
        <CardDescription>
          The toolchain workers are told before they start: runtime, setup
          commands in order, and anything else a fresh checkout needs. Rides
          the work context and the AGENTS.md export; leave empty to say
          nothing.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-3">
        <div className="grid gap-2">
          <Label htmlFor="env-runtime">Language / runtime and version</Label>
          <Input
            id="env-runtime"
            value={runtime}
            onChange={(e) => setRuntime(e.target.value)}
            placeholder="e.g. Python 3.12, Node 22"
          />
        </div>
        <div className="grid gap-2">
          <Label htmlFor="env-setup">Setup commands (one per line, in order)</Label>
          <Textarea
            id="env-setup"
            value={setup}
            onChange={(e) => setSetup(e.target.value)}
            placeholder={"npm install\ncp .env.example .env"}
            rows={3}
            className="font-mono text-xs"
          />
        </div>
        <div className="grid gap-2">
          <Label htmlFor="env-notes">Notes</Label>
          <Textarea
            id="env-notes"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="Anything else the environment needs — env vars, services, versions to avoid…"
            rows={2}
          />
        </div>
        <div className="flex items-center gap-2">
          <Button
            type="button"
            size="sm"
            disabled={!dirty || saving}
            onClick={save}
          >
            {saving && <Loader2 className="size-3.5 animate-spin" />}
            Save environment
          </Button>
          {error && (
            <p className="text-xs font-medium text-destructive">{error}</p>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
