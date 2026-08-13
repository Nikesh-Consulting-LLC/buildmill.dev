"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { CheckCircle2, CircleDashed, HelpCircle, Loader2 } from "lucide-react";
import { apiCall } from "@/lib/api";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

type DeploymentState = {
  id: string;
  name: string;
  state: "deployed" | "not-deployed" | "zip" | "never" | "unknown";
  since: string | null;
};

/** US-1.48: where is this issue's merged change live? */
export function IssueDeploymentsPanel({
  issueId,
  projectId,
}: {
  issueId: string;
  projectId: string;
}) {
  const [data, setData] = useState<{
    merge_sha: string | null;
    deployments: DeploymentState[];
  } | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const resp = (await apiCall(`/api/v1/issues/${issueId}/deployments`)) as {
          merge_sha: string | null;
          deployments: DeploymentState[];
        };
        if (!cancelled) setData(resp);
      } catch (e) {
        if (!cancelled) setError((e as Error).message);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [issueId]);

  // No merged change or no deployments configured — show nothing new.
  if (data && (!data.merge_sha || data.deployments.length === 0)) return null;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Deployments</CardTitle>
        <CardDescription>
          Whether this work item&apos;s merged change is live on each environment.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {error ? (
          <p className="text-xs text-muted-foreground">
            Deployment status unavailable: {error}
          </p>
        ) : !data ? (
          <p className="inline-flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="size-3.5 animate-spin" />
            Checking environments…
          </p>
        ) : (
          <ul className="grid gap-1.5">
            {data.deployments.map((d) => (
              <li key={d.id} className="flex items-center gap-2 text-sm">
                {d.state === "deployed" ? (
                  <CheckCircle2 className="size-4 shrink-0 text-emerald-600" />
                ) : d.state === "unknown" || d.state === "zip" ? (
                  <HelpCircle className="size-4 shrink-0 text-muted-foreground" />
                ) : (
                  <CircleDashed className="size-4 shrink-0 text-muted-foreground" />
                )}
                <Link
                  href={`/projects/${projectId}/deployments/${d.id}`}
                  className="font-medium underline-offset-4 hover:underline"
                >
                  {d.name}
                </Link>
                <span className="text-xs text-muted-foreground">
                  {d.state === "deployed" && d.since
                    ? `live since ${new Date(d.since).toLocaleString(undefined, {
                        month: "short",
                        day: "numeric",
                        hour: "2-digit",
                        minute: "2-digit",
                      })}`
                    : d.state === "deployed"
                      ? "live"
                      : d.state === "not-deployed"
                        ? "not yet deployed"
                        : d.state === "never"
                          ? "never deployed"
                          : d.state === "zip"
                            ? "n/a (zip deploy)"
                            : "unknown"}
                </span>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
