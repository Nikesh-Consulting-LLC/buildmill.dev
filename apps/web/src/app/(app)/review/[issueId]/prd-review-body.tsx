"use client";

import { useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import { Loader2, Pencil } from "lucide-react";
import { apiFetch } from "@/lib/api";
import {
  EMPTY_PRD_SECTIONS,
  PRD_SECTIONS,
  parsePrdSections,
  serializePrdSections,
  type PrdSections,
} from "@/lib/prd";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { MarkdownEditor } from "@/components/markdown-editor";
import { MarkdownView } from "@/components/markdown-view";
import { useActivitySession } from "@/lib/use-activity-session";

/** US-12.2: the draft PRD as it reads on the review surface, with the same
 * per-section edit the work item panel offers — a manager must not have to
 * leave review to fix a wording problem they just spotted. Section parsing
 * and serialization come from lib/prd, so both surfaces write an identical
 * document shape. */
export function PrdReviewBody({
  issueId,
  artifactId,
  content,
  version,
}: {
  issueId: string;
  artifactId: string;
  content: string;
  version: number;
}) {
  const router = useRouter();
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sections, setSections] = useState<PrdSections>(EMPTY_PRD_SECTIONS);
  // US-62.6: how long this edit session was actually active, pause-aware.
  useActivitySession(editing, "artifact-edit", issueId);

  function startEditing() {
    setSections(parsePrdSections(content));
    setEditing(true);
  }

  async function save() {
    setError(null);
    setBusy(true);
    try {
      await apiFetch(`/api/v1/artifacts/${artifactId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: serializePrdSections(sections) }),
      });
      setEditing(false);
      router.refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between space-y-0">
        <div className="space-y-1.5">
          <CardTitle className="text-base">PRD · draft v{version}</CardTitle>
          <CardDescription>
            The product requirements this feature would ship against.
          </CardDescription>
        </div>
        {!editing && (
          <Button variant="outline" size="sm" onClick={startEditing}>
            <Pencil className="size-3.5" />
            Edit
          </Button>
        )}
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {editing ? (
          <>
            {PRD_SECTIONS.map((section) => (
              <div key={section.key} className="grid gap-2">
                <Label htmlFor={`prd-review-${section.key}`}>
                  {section.heading}
                </Label>
                <MarkdownEditor
                  id={`prd-review-${section.key}`}
                  value={sections[section.key]}
                  onChange={(v) =>
                    setSections((prev) => ({ ...prev, [section.key]: v }))
                  }
                  rows={5}
                />
              </div>
            ))}
            <div className="flex items-center gap-2">
              <Button onClick={save} disabled={busy}>
                {busy && <Loader2 className="size-4 animate-spin" />}
                Save
              </Button>
              <Button
                variant="outline"
                onClick={() => setEditing(false)}
                disabled={busy}
              >
                Cancel
              </Button>
            </div>
          </>
        ) : (
          <MarkdownView>{content}</MarkdownView>
        )}
        {error && (
          <p className="text-sm font-medium text-destructive">{error}</p>
        )}
      </CardContent>
    </Card>
  );
}
