"use client";

import { useState } from "react";
import { PencilRuler, RefreshCw } from "lucide-react";
import { apiCall } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

/** US-48.5: rebuild the wireframe tree from what the app holds.
 *
 * Three things the per-hand-back write cannot do, which is why this button
 * exists: restyle existing wireframes when the kit changes, remove the file of
 * a story that was abandoned or redrawn as "no UI surface", and produce an
 * index. It is also the retry after a hand-back-time write failed. */
export function WireframesCard({ projectId }: { projectId: string }) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function syncNow() {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const result = (await apiCall(
        `/api/v1/projects/${projectId}/wireframes/sync`,
        { method: "POST" }
      )) as {
        commit_sha?: string;
        unchanged?: boolean;
        skipped?: string;
        drawn?: number;
        no_ui_surface?: number;
        deleted?: string[];
      };
      if (result.skipped) {
        setMessage(`Skipped: ${result.skipped}`);
      } else if (result.unchanged) {
        setMessage("Already up to date — nothing to commit.");
      } else {
        const removed = result.deleted?.length
          ? `, ${result.deleted.length} removed`
          : "";
        setMessage(
          `Synced ${result.drawn ?? 0} wireframe${
            result.drawn === 1 ? "" : "s"
          }${
            result.no_ui_surface
              ? ` (${result.no_ui_surface} with no UI surface)`
              : ""
          }${removed} — commit ${(result.commit_sha ?? "").slice(0, 7)}.`
        );
      }
    } catch (e) {
      setError((e as Error).message);
    }
    setBusy(false);
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <PencilRuler className="size-4 text-muted-foreground" />
          Wireframes in the repo
        </CardTitle>
        <CardDescription>
          Every screen an agent has drawn is written into the repo under{" "}
          <code>docs/wireframes/</code>, with an index and the kit they render
          through. Each page opens straight from disk. Build Mill owns that
          folder — anything it stops producing is removed, so what is there is
          current. Syncing rebuilds it from the app, which is also how a kit
          change reaches wireframes drawn against an older one.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-wrap items-center gap-3">
        <Button size="sm" variant="outline" disabled={busy} onClick={syncNow}>
          <RefreshCw className={busy ? "size-3.5 animate-spin" : "size-3.5"} />
          Sync wireframes
        </Button>
        {message && <p className="text-sm text-muted-foreground">{message}</p>}
        {error && <p className="text-sm text-destructive">{error}</p>}
      </CardContent>
    </Card>
  );
}
