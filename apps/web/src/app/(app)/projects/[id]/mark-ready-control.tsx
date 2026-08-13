"use client";

import { useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import { BadgeCheck, CircleDashed, Loader2, TriangleAlert } from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

/** US-7.4 / US-7.5: a project-level "mark as ready" control with a sticky,
 * honest badge. Editing after ready never auto-revokes it; when content has
 * changed since the ready stamp, an "edited since marked ready" nudge invites
 * re-confirmation. Writes projects.<prefix>_ready_at / _ready_by under RLS. */
export function MarkReadyControl({
  projectId,
  prefix,
  readyAt,
  readyByName,
  editedSince,
}: {
  projectId: string;
  prefix: "guidelines" | "worker_instructions";
  readyAt: string | null;
  readyByName: string | null;
  editedSince: boolean;
}) {
  const router = useRouter();
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function markReady() {
    setSaving(true);
    setError(null);
    const supabase = createClient();
    const {
      data: { user },
    } = await supabase.auth.getUser();
    const { error: dbError } = await supabase
      .from("projects")
      .update({
        [`${prefix}_ready_at`]: new Date().toISOString(),
        [`${prefix}_ready_by`]: user?.id ?? null,
      })
      .eq("id", projectId);
    setSaving(false);
    if (dbError) {
      setError(dbError.message);
      return;
    }
    router.refresh();
  }

  const ready = !!readyAt;
  const when = readyAt
    ? new Date(readyAt).toLocaleDateString(undefined, {
        month: "short",
        day: "numeric",
        year: "numeric",
      })
    : null;

  return (
    <div className="flex flex-wrap items-center gap-2">
      {ready ? (
        <Badge className="gap-1 border-emerald-200 bg-emerald-100 font-normal text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950 dark:text-emerald-300">
          <BadgeCheck className="size-3.5" />
          Ready
          <span className="font-normal opacity-80">
            · {when}
            {readyByName ? ` · ${readyByName}` : ""}
          </span>
        </Badge>
      ) : (
        <Badge variant="secondary" className="gap-1 font-normal">
          <CircleDashed className="size-3.5" />
          Not ready
        </Badge>
      )}
      {ready && editedSince && (
        <span className="inline-flex items-center gap-1 text-xs text-amber-700 dark:text-amber-400">
          <TriangleAlert className="size-3.5" />
          Edited since marked ready
        </span>
      )}
      <Button type="button" variant="outline" size="sm" onClick={markReady} disabled={saving}>
        {saving && <Loader2 className="size-4 animate-spin" />}
        {ready && editedSince ? "Re-confirm ready" : ready ? "Re-mark ready" : "Mark as ready"}
      </Button>
      {error && <span className="text-xs font-medium text-destructive">{error}</span>}
    </div>
  );
}
