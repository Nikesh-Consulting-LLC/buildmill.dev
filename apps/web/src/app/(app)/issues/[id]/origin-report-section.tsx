import Link from "next/link";
import { Bug, MessageSquare } from "lucide-react";
import { createClient } from "@/lib/supabase/server";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

/**
 * US-16.7: a bug promoted from an app report says where it came from.
 *
 * The generated description carries the stack trace, but not the *shape* of
 * the incident — how many times it happened, over what window, whether a human
 * or a crash handler filed it. Someone working the bug needs that, and it is
 * one link away rather than copied into the body where it would immediately
 * go stale as the crash keeps happening.
 */
export async function OriginReportSection({ issueId }: { issueId: string }) {
  const supabase = await createClient();
  const { data: report } = await supabase
    .from("app_issues")
    .select(
      "id, source, occurrence_count, first_seen_at, last_seen_at, reporter_name, reporter_email",
    )
    .eq("promoted_issue_id", issueId)
    .maybeSingle();

  if (!report) return null;

  const automated = report.source === "automated";
  const stamp = (value: string | null) =>
    value ? new Date(value).toLocaleString() : "—";

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          {automated ? (
            <Bug className="size-4 text-destructive" />
          ) : (
            <MessageSquare className="size-4 text-blue-600" />
          )}
          Reported by the app
        </CardTitle>
      </CardHeader>
      <CardContent className="grid gap-3">
        <div className="flex flex-wrap gap-1.5">
          <Badge variant="outline">
            {automated ? "Automatic crash report" : "Submitted by a user"}
          </Badge>
          {automated && (
            <Badge variant="outline">{report.occurrence_count} occurrences</Badge>
          )}
          <Badge variant="outline">first seen {stamp(report.first_seen_at)}</Badge>
          <Badge variant="outline">last seen {stamp(report.last_seen_at)}</Badge>
        </div>
        {(report.reporter_name || report.reporter_email) && (
          <p className="text-sm text-muted-foreground">
            Reported by {report.reporter_name ?? "someone"}
            {report.reporter_email ? ` · ${report.reporter_email}` : ""}
          </p>
        )}
        <Link
          href={`/reports?report=${report.id}`}
          className="text-sm font-medium text-primary hover:underline"
        >
          Open the original report →
        </Link>
      </CardContent>
    </Card>
  );
}
