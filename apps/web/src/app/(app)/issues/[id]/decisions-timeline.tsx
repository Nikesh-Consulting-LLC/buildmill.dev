import Link from "next/link";
import { Gavel } from "lucide-react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { EmptyState } from "@/components/empty-state";
import { StatusBadge, type IssueStatus } from "@/components/status-badge";
import {
  formatWhen,
  humanizeToken,
  subjectHref,
  subjectLabel,
  type ApprovalRow,
} from "@/lib/approvals";

/** Every gate decision on this issue, chronologically (us-2.7). */
export function DecisionsTimeline({
  issueId,
  approvals,
  actorNames,
}: {
  issueId: string;
  approvals: ApprovalRow[];
  actorNames: Record<string, string>;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <Gavel className="size-4 text-muted-foreground" />
          Decisions
        </CardTitle>
        <CardDescription>
          Every gate decision on this issue — who decided what, and why.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {approvals.length === 0 ? (
          <EmptyState
            icon={Gavel}
            title="No decisions yet"
            description="Gate decisions appear here as this work item moves through PRD, plan, review, and release."
          />
        ) : (
          <ol className="relative grid gap-4 border-l pl-4">
            {approvals.map((a) => {
              const href = subjectHref(a.subject_type, a.subject_id, issueId);
              const label = subjectLabel(a.subject_type, a.subject_id);
              return (
                <li key={a.id} className="grid gap-1 text-sm">
                  <span className="absolute -left-[5px] mt-1.5 size-2.5 rounded-full border bg-background" />
                  <span className="flex flex-wrap items-center gap-2">
                    <span className="font-medium">{humanizeToken(a.gate)}</span>
                    <StatusBadge status={a.decision as IssueStatus} />
                  </span>
                  <span className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
                    <span>
                      {/* US-17.5: an automated decision reads as such, never as
                          a person — an auto-merge that no human reviewed is
                          plainly marked. */}
                      {a.auto_approved
                        ? "Auto-approved · project setting"
                        : a.actor
                          ? (actorNames[a.actor] ?? a.actor)
                          : "Unknown actor"}
                    </span>
                    <span aria-hidden>·</span>
                    <span>{formatWhen(a.created_at)}</span>
                    {label && (
                      <>
                        <span aria-hidden>·</span>
                        {href ? (
                          <Link
                            href={href}
                            className="font-mono underline-offset-4 hover:underline"
                          >
                            {label}
                          </Link>
                        ) : (
                          <span className="font-mono">{label}</span>
                        )}
                      </>
                    )}
                  </span>
                  {a.comment && (
                    <p className="rounded-md border bg-muted/40 px-2.5 py-1.5 text-sm">
                      {a.comment}
                    </p>
                  )}
                </li>
              );
            })}
          </ol>
        )}
      </CardContent>
    </Card>
  );
}
