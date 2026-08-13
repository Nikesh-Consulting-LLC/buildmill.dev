"use client";

import { useState } from "react";
import { GitBranch, Loader2 } from "lucide-react";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";

type SaveInstructionsResult = {
  agents_md: { html_url: string; commit_sha: string };
  claude_md: { html_url: string; commit_sha: string };
};

export function SaveInstructionsButton({ projectId }: { projectId: string }) {
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<SaveInstructionsResult | null>(null);

  async function handleSave() {
    setError(null);
    setResult(null);
    setSaving(true);
    try {
      const body = (await apiFetch(
        `/api/v1/projects/${projectId}/guidelines/save-instructions`,
        { method: "POST" }
      )) as SaveInstructionsResult;
      setResult(body);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't save instructions.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="flex flex-col items-start gap-1">
      <Button variant="outline" size="sm" disabled={saving} onClick={handleSave}>
        {saving ? (
          <Loader2 className="size-4 animate-spin" />
        ) : (
          <GitBranch className="size-4" />
        )}
        Save Instructions
      </Button>
      {error && <p className="text-xs font-medium text-destructive">{error}</p>}
      {result && (
        <p className="text-xs text-muted-foreground">
          Committed{" "}
          <a
            href={result.agents_md.html_url}
            target="_blank"
            rel="noreferrer"
            className="underline underline-offset-2"
          >
            AGENTS.md
          </a>{" "}
          and{" "}
          <a
            href={result.claude_md.html_url}
            target="_blank"
            rel="noreferrer"
            className="underline underline-offset-2"
          >
            CLAUDE.md
          </a>
        </p>
      )}
    </div>
  );
}
