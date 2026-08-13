"use client";

import { useState } from "react";
import { ScrollText } from "lucide-react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { EmptyState } from "@/components/empty-state";

export type ConfigEvent = {
  id: number;
  actor: string;
  event: string;
  areas: string[];
  detail: { previous_script?: string; name?: string; action?: string } | null;
  created_at: string;
};

/** US-1.49: the config audit trail — who changed what, when. */
export function HistoryCard({ events }: { events: ConfigEvent[] }) {
  const [expandedId, setExpandedId] = useState<number | null>(null);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Configuration history</CardTitle>
        <CardDescription>
          Every change to this deployment&apos;s configuration — runs are
          audited in the run history above; this is the trail for the
          definition they execute.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {events.length === 0 ? (
          <EmptyState
            icon={ScrollText}
            title="No changes recorded"
            description="Config edits from now on land here — who, when, and which areas."
          />
        ) : (
          <ul className="grid gap-1.5">
            {events.map((e) => (
              <li key={e.id} className="rounded-md border px-3 py-2 text-sm">
                <div className="flex items-center justify-between gap-3">
                  <span className="flex min-w-0 flex-wrap items-center gap-1.5">
                    <span className="font-medium">{e.event}</span>
                    {(e.areas ?? []).map((a) => (
                      <Badge key={a} variant="secondary" className="font-normal">
                        {a}
                        {a === "env" && e.detail?.name
                          ? `: ${e.detail.name} ${e.detail.action ?? ""}`
                          : ""}
                      </Badge>
                    ))}
                  </span>
                  <span className="shrink-0 text-xs text-muted-foreground">
                    {e.actor} ·{" "}
                    {new Date(e.created_at).toLocaleString(undefined, {
                      month: "short",
                      day: "numeric",
                      hour: "2-digit",
                      minute: "2-digit",
                    })}
                  </span>
                </div>
                {e.detail?.previous_script !== undefined && (
                  <div className="mt-1">
                    <button
                      type="button"
                      className="text-xs text-muted-foreground underline-offset-4 hover:underline"
                      onClick={() =>
                        setExpandedId(expandedId === e.id ? null : e.id)
                      }
                    >
                      {expandedId === e.id ? "Hide" : "Show"} previous script
                    </button>
                    {expandedId === e.id && (
                      <pre className="mt-1 max-h-48 overflow-auto rounded-md border bg-muted/30 p-2 font-mono text-xs whitespace-pre-wrap">
                        {e.detail.previous_script || "(empty script)"}
                      </pre>
                    )}
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
