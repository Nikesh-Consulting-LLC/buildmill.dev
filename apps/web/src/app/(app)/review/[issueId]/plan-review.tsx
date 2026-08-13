import { MarkdownView } from "@/components/markdown-view";
import { ClipboardList, FlaskConical } from "lucide-react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

export type PlanArtifact = {
  id: string;
  kind: string;
  content: string;
  version: number;
};


/** The draft plan + test plan for an issue in plan-review (us-2.5). */
export function PlanReview({ artifacts }: { artifacts: PlanArtifact[] }) {
  const plan = artifacts.find((a) => a.kind === "plan");
  const testPlan = artifacts.find((a) => a.kind === "test_plan");

  return (
    <div className="flex flex-col gap-6">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <ClipboardList className="size-4 text-muted-foreground" />
            Plan
          </CardTitle>
          <CardDescription>
            The approach the factory intends to take.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {plan ? (
            <MarkdownView>{plan.content}</MarkdownView>
          ) : (
            <p className="text-sm text-muted-foreground">No plan draft found.</p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <FlaskConical className="size-4 text-muted-foreground" />
            Test plan
          </CardTitle>
          <CardDescription>
            Approving materializes these into test cases.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {testPlan ? (
            <MarkdownView>{testPlan.content}</MarkdownView>
          ) : (
            <p className="text-sm text-muted-foreground">
              No test plan draft found.
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
