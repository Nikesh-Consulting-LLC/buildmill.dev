"use client";

import { useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import { Workflow } from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";

// US-86.1: the build-mode radio and the Concurrency section retired. Routing
// is two switches; execution has no switch at all — a project works one item
// at a time, start to merge, always (the serial law lives in
// issue_hold_reason, migration 247). The DB mirrors the legacy
// build_mode/sequential_only columns from these switches via trigger.
const ROUTING: {
  column: "follow_build_order" | "route_feature_as_one";
  label: string;
  help: string;
  uncheckedHelp: string;
}[] = [
  {
    column: "follow_build_order",
    label: "Follow the build order (Epic → Feature → Story)",
    help: "Items go in hierarchy order: an item whose predecessors aren't done yet waits its turn.",
    uncheckedHelp:
      "Route anything, any time, in any order you choose. Careful: coding an item before an earlier one merges can conflict at merge — the risk is yours. One item still runs at a time, start to merge.",
  },
  {
    column: "route_feature_as_one",
    label: "Route the feature as one",
    help: "A feature's stories travel together: one action plans them all, one action builds them all as a single run and one PR. Stories carry no route buttons of their own.",
    uncheckedHelp:
      "Stories carry their own route buttons and go through plan and code individually.",
  },
];

const SWITCHES: {
  column: "auto_approve_prd" | "auto_approve_plan" | "auto_approve_code";
  label: string;
  help: string;
}[] = [
  {
    column: "auto_approve_prd",
    label: "Auto-approve PRDs",
    help: "When a PRD is submitted, approve it and start the story breakdown automatically — no review.",
  },
  {
    column: "auto_approve_plan",
    label: "Auto-approve plans",
    help: "When a plan is submitted, approve it and dispatch the code run automatically — no review.",
  },
  {
    column: "auto_approve_code",
    label: "Auto-approve & merge code",
    help: "When code is submitted, approve it — which MERGES the pull request — with no human review. Deployment is never automatic.",
  },
];

export function TaskProcessingCard({
  projectId,
  followBuildOrder,
  routeFeatureAsOne,
  autoApprovePrd,
  autoApprovePlan,
  autoApproveCode,
}: {
  projectId: string;
  followBuildOrder: boolean;
  routeFeatureAsOne: boolean;
  autoApprovePrd: boolean;
  autoApprovePlan: boolean;
  autoApproveCode: boolean;
}) {
  const router = useRouter();
  const [routing, setRouting] = useState({
    follow_build_order: followBuildOrder,
    route_feature_as_one: routeFeatureAsOne,
  });
  const [flags, setFlags] = useState({
    auto_approve_prd: autoApprovePrd,
    auto_approve_plan: autoApprovePlan,
    auto_approve_code: autoApproveCode,
  });
  const [error, setError] = useState<string | null>(null);

  async function save(patch: Record<string, unknown>) {
    setError(null);
    const supabase = createClient();
    const { error: dbError } = await supabase
      .from("projects")
      .update(patch)
      .eq("id", projectId);
    if (dbError) {
      setError(dbError.message);
      return false;
    }
    router.refresh();
    return true;
  }

  async function toggleRouting(
    column: "follow_build_order" | "route_feature_as_one",
    checked: boolean
  ) {
    setRouting((r) => ({ ...r, [column]: checked }));
    if (!(await save({ [column]: checked })))
      setRouting((r) => ({ ...r, [column]: !checked }));
  }

  async function toggle(
    column: "auto_approve_prd" | "auto_approve_plan" | "auto_approve_code",
    checked: boolean
  ) {
    setFlags((f) => ({ ...f, [column]: checked }));
    if (!(await save({ [column]: checked })))
      setFlags((f) => ({ ...f, [column]: !checked }));
  }

  return (
    <Card className="min-w-0">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <Workflow className="size-4 text-muted-foreground" />
          Task processing
        </CardTitle>
        <CardDescription>
          How this project&apos;s work is routed, and whether the factory
          promotes it past your gates on its own. The factory always works one
          item at a time, start to merge — everything queued behind it waits,
          visibly.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-6">
        <div className="grid gap-3">
          <Label>Routing</Label>
          {ROUTING.map((s) => (
            <label
              key={s.column}
              className="flex cursor-pointer items-start gap-3 rounded-lg border px-3 py-2.5"
            >
              <Checkbox
                checked={routing[s.column]}
                onCheckedChange={(checked) =>
                  toggleRouting(s.column, checked === true)
                }
                className="mt-0.5"
              />
              <span className="min-w-0">
                <span className="block text-sm font-medium">{s.label}</span>
                <span className="block text-xs text-muted-foreground">
                  {routing[s.column] ? s.help : s.uncheckedHelp}
                </span>
              </span>
            </label>
          ))}
        </div>

        <div className="grid gap-3">
          <Label>Auto-approve</Label>
          <p className="-mt-1 text-xs text-muted-foreground">
            Off by default. When on, the factory clears that gate the moment its
            run is submitted and moves work to the next step on its own — it
            never rejects, and never deploys.
          </p>
          {SWITCHES.map((s) => (
            <label
              key={s.column}
              className="flex cursor-pointer items-start gap-3 rounded-lg border px-3 py-2.5"
            >
              <Checkbox
                checked={flags[s.column]}
                onCheckedChange={(checked) => toggle(s.column, checked)}
                className="mt-0.5"
              />
              <span className="min-w-0">
                <span className="block text-sm font-medium">{s.label}</span>
                <span className="block text-xs text-muted-foreground">
                  {s.help}
                </span>
              </span>
            </label>
          ))}
        </div>

        {error && <p className="text-sm text-destructive">{error}</p>}
      </CardContent>
    </Card>
  );
}
