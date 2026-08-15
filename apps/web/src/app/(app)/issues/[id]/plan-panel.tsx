import { MarkdownView } from "@/components/markdown-view";
import { ClipboardList } from "lucide-react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { ReplanButton } from "./replan-button";

export type PlanArtifactRow = {
  id: string;
  kind: string;
  content: string;
  version: number;
  status: string;
};

const ACTIVE_STATUSES = new Set(["queued", "running", "planning", "plan-review"]);

/** The latest plan/test_plan artifacts for an issue (us-2.5), with a
 * Re-plan action when no run is currently active. */
export function PlanPanel({
  issueId,
  status,
  artifacts,
  isBug = false,
}: {
  issueId: string;
  status: string;
  artifacts: PlanArtifactRow[];
  /** us-96.5: a bug's plan artifact is an RCA (us-96.2) — same panel,
   * honest heading. */
  isBug?: boolean;
}) {
  const plan = artifacts.find((a) => a.kind === "plan");
  const testPlan = artifacts.find((a) => a.kind === "test_plan");
  if (!plan && !testPlan) return null;

  return (
    <Card id="plan">
      {/* US-2.16: anchor targets for audit/decisions deep links. */}
      {artifacts.map((a) => (
        <span key={a.id} id={`artifact-${a.id}`} className="block scroll-mt-20" />
      ))}
      <CardHeader className="flex flex-row items-center justify-between space-y-0">
        <div className="space-y-1.5">
          <CardTitle className="flex items-center gap-2 text-base">
            <ClipboardList className="size-4 text-muted-foreground" />
            {isBug ? "Root cause analysis" : "Plan"}
          </CardTitle>
          <CardDescription>
            {isBug
              ? "The diagnosis and proposed fix the factory is working from."
              : "The approach and test plan the factory is working from."}
          </CardDescription>
        </div>
        <div className="flex items-center gap-2">
          {!ACTIVE_STATUSES.has(status) && (
            <ReplanButton issueId={issueId} status={status} />
          )}
        </div>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {plan && (
          <details className="rounded-md border p-3" open>
            <summary className="flex cursor-pointer select-none items-center justify-between text-sm font-medium">
              <span>Plan · v{plan.version}</span>
              <Badge variant="secondary" className="capitalize">
                {plan.status}
              </Badge>
            </summary>
            <MarkdownView className="mt-3">{plan.content}</MarkdownView>
          </details>
        )}
        {testPlan && (
          <details className="rounded-md border p-3">
            <summary className="flex cursor-pointer select-none items-center justify-between text-sm font-medium">
              <span>Test plan · v{testPlan.version}</span>
              <Badge variant="secondary" className="capitalize">
                {testPlan.status}
              </Badge>
            </summary>
            <MarkdownView className="mt-3">{testPlan.content}</MarkdownView>
          </details>
        )}
      </CardContent>
    </Card>
  );
}
