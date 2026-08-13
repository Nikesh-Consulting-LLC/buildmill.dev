"use client";

import { useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import { Loader2 } from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { MarkdownEditor } from "@/components/markdown-editor";
import { SpendSummary } from "@/components/spend-summary";

/** US-7.8: the Project Summary — a long-form markdown description of what the
 * project is and its goals, served to workers as run context.
 * US-20.2: the AI setup brainstorm this used to seed was withdrawn. */
export function ProjectSummaryCard({
  orgId,
  projectId,
  summary,
}: {
  orgId: string;
  projectId: string;
  summary: string | null;
}) {
  const router = useRouter();
  const [content, setContent] = useState(summary ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const dirty = content !== (summary ?? "");
  const hasSummary = (summary ?? "").trim().length > 0;

  async function save() {
    setSaving(true);
    setError(null);
    const supabase = createClient();
    const { error: dbError } = await supabase
      .from("projects")
      .update({ summary: content })
      .eq("id", projectId);
    setSaving(false);
    if (dbError) {
      setError(dbError.message);
      return;
    }
    router.refresh();
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Project Summary</CardTitle>
        <CardDescription>
          What this project is, who it&apos;s for, and its goals. Every agent
          run on this project is given it as context.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-3">
        {/* US-33.3: project spend, on the project. Same query as the Spend
            page, so the two can never disagree. */}
        <SpendSummary orgId={orgId} projectId={projectId} label="This project" />
        <MarkdownEditor
          rows={6}
          value={content}
          onChange={setContent}
          orgId={orgId}
          // Already-written summaries open on Preview; a blank one on Write.
          defaultTab={hasSummary ? "preview" : "write"}
          placeholder="Describe what this project is, who it's for, and the goals an agent should keep in mind…"
        />
        <div className="flex items-center justify-between gap-2">
          {error ? (
            <p className="text-sm font-medium text-destructive">{error}</p>
          ) : (
            <span />
          )}
          {dirty && (
            <Button size="sm" disabled={saving} onClick={save}>
              {saving && <Loader2 className="size-4 animate-spin" />}
              Save summary
            </Button>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
