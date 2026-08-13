"use client";

import { useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import { Loader2, Plus, Puzzle } from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

export type SuggestedCase = {
  id: string;
  org_id: string;
  project_id: string;
  issue_id: string | null;
  title: string;
  steps: string;
  expected_result: string;
  source: string;
  test_types: string[];
  environments: string[];
  module_name: string;
};

/** US-82.3: manual regression cases whose module this release touched but
 * that are not in its test set. Suggested, never auto-attached — the manager
 * clicks, and an attached case then gates sign-off like any inherited one. */
export function ReleaseModuleSuggestions({
  releaseId,
  touchedModules,
  suggestions,
}: {
  releaseId: string;
  touchedModules: string[];
  suggestions: SuggestedCase[];
}) {
  const router = useRouter();
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (!touchedModules.length || !suggestions.length) return null;

  async function attach(c: SuggestedCase) {
    setBusyId(c.id);
    setError(null);
    const supabase = createClient();
    const { error: dbError } = await supabase.from("test_cases").insert({
      org_id: c.org_id,
      project_id: c.project_id,
      release_id: releaseId,
      issue_id: c.issue_id,
      title: c.title,
      steps: c.steps,
      expected_result: c.expected_result,
      source: c.source,
      test_types: c.test_types,
      environments: c.environments,
      status: "active",
    });
    setBusyId(null);
    if (dbError) {
      setError(dbError.message);
      return;
    }
    router.refresh();
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <Puzzle className="size-4" />
          Suggested regression cases
        </CardTitle>
        <CardDescription>
          This release touched{" "}
          {touchedModules.map((m) => (
            <Badge key={m} variant="secondary" className="mr-1">
              {m}
            </Badge>
          ))}
          — these manual cases are tagged with those modules but are not in
          the test set. Attaching one gates sign-off like any other case.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <ul className="grid gap-2">
          {suggestions.map((c) => (
            <li
              key={c.id}
              className="flex flex-wrap items-center gap-2 rounded-md border px-3 py-1.5 text-sm"
            >
              <span className="min-w-0 flex-1 truncate font-medium">
                {c.title}
              </span>
              <Badge variant="outline">{c.module_name}</Badge>
              <Button
                variant="outline"
                size="xs"
                disabled={busyId === c.id}
                onClick={() => attach(c)}
                title="Copy this case into the release's test set"
              >
                {busyId === c.id ? (
                  <Loader2 className="size-3 animate-spin" />
                ) : (
                  <Plus className="size-3" />
                )}
                Attach
              </Button>
            </li>
          ))}
        </ul>
        {error && (
          <p className="mt-2 text-sm font-medium text-destructive">{error}</p>
        )}
      </CardContent>
    </Card>
  );
}
