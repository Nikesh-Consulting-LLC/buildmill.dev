"use client";

import { useState } from "react";
import { createClient } from "@/lib/supabase/client";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Label } from "@/components/ui/label";

const EVENTS: { key: string; label: string }[] = [
  { key: "started", label: "Run started" },
  { key: "succeeded", label: "Succeeded" },
  { key: "failed", label: "Failed" },
  { key: "cancelled", label: "Cancelled" },
  { key: "rolled_back", label: "Rolled back" },
  // US-81.2/82.1: a test suite run against this deployment did not pass.
  { key: "suite-failed", label: "Test suite failed" },
];

export const DEFAULT_EVENTS = ["failed", "rolled_back"];

/** US-1.44 per-deployment event selection. No row = the default set. */
export function NotificationsCard({
  orgId,
  deploymentId,
  initialEvents,
  hasEndpoints,
}: {
  orgId: string;
  deploymentId: string;
  initialEvents: string[] | null;
  hasEndpoints: boolean;
}) {
  const [events, setEvents] = useState<string[]>(initialEvents ?? DEFAULT_EVENTS);
  const [error, setError] = useState<string | null>(null);

  async function toggle(key: string) {
    const next = events.includes(key)
      ? events.filter((e) => e !== key)
      : [...events, key];
    setEvents(next);
    setError(null);
    const supabase = createClient();
    const { error: dbError } = await supabase.from("deployment_notifications").upsert({
      deployment_id: deploymentId,
      org_id: orgId,
      events: next,
    });
    if (dbError) setError(dbError.message);
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Notifications</CardTitle>
        <CardDescription>
          {hasEndpoints
            ? "Which run outcomes notify the org's webhook endpoints (configured in Settings)."
            : "No webhook endpoints exist yet — nothing is sent. Add one in Settings first."}
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="flex flex-wrap gap-x-6 gap-y-2">
          {EVENTS.map((e) => (
            <Label
              key={e.key}
              className="flex cursor-pointer items-center gap-2 text-sm font-normal"
            >
              <input
                type="checkbox"
                className="size-4 accent-primary"
                checked={events.includes(e.key)}
                onChange={() => toggle(e.key)}
              />
              {e.label}
            </Label>
          ))}
        </div>
        {error && (
          <p className="mt-2 text-sm font-medium text-destructive">{error}</p>
        )}
      </CardContent>
    </Card>
  );
}
