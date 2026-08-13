"use client";

import { useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import { ClipboardList, Eye, Loader2, Pencil } from "lucide-react";
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
import { MarkdownView } from "@/components/markdown-view";

/** US-5.11: the work item's living instruction set — seeded at dispatch,
 * manager-editable at any stage, read live by workers over MCP. Attached
 * to the item (not the run) so nothing is lost on retries or hand-offs. */
export function InstructionSetPanel({
  issueId,
  orgId,
  instructionSet,
  collapsible = false,
}: {
  issueId: string;
  orgId: string;
  instructionSet: string | null;
  /** US-49.6: with no run in flight this is reference material, not the
   * thing the manager came for — so it folds away rather than pushing the
   * run list down the page. */
  collapsible?: boolean;
}) {
  const router = useRouter();
  const initial = instructionSet ?? "";
  const [content, setContent] = useState(initial);
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const dirty = content !== initial;

  async function handleSave() {
    setError(null);
    setSaving(true);
    const supabase = createClient();
    const { error: dbError } = await supabase
      .from("issues")
      .update({ instruction_set: content })
      .eq("id", issueId);
    if (dbError) {
      setSaving(false);
      setError(dbError.message);
      return;
    }
    // Audit the edit (us-2.7 pattern) — visible to the next MCP read.
    await supabase.from("issue_events").insert({
      org_id: orgId,
      issue_id: issueId,
      type: "instructions-updated",
      payload: { length: content.length },
    });
    setSaving(false);
    setEditing(false);
    router.refresh();
  }

  const body = (
    <div className="flex flex-col gap-3">
      {editing ? (
        <>
          <MarkdownEditor
            rows={12}
            value={content}
            onChange={setContent}
            orgId={orgId}
            placeholder="What the agent on this item is expected to do — filled automatically at first dispatch if left empty."
          />
          {dirty && (
            <div className="flex justify-end">
              <Button size="sm" disabled={saving} onClick={handleSave}>
                {saving && <Loader2 className="size-4 animate-spin" />}
                Save
              </Button>
            </div>
          )}
        </>
      ) : content.trim() ? (
        <MarkdownView>{content}</MarkdownView>
      ) : (
        <p className="text-sm text-muted-foreground">
          None yet — you will see these, and be able to change them, when you
          dispatch this item.
        </p>
      )}
      {error && <p className="text-sm font-medium text-destructive">{error}</p>}
    </div>
  );

  return (
    <Card id="instruction-set">
      <CardHeader className="flex flex-row items-center justify-between space-y-0">
        <div className="space-y-1.5">
          <CardTitle className="flex items-center gap-2 text-base">
            <ClipboardList className="size-4 text-muted-foreground" />
            Instruction set
          </CardTitle>
          <CardDescription>
            {/* US-49.6: a worker re-reads this while it works, which is the
                one thing nothing else on the page can do — everything else it
                was given was frozen into the run at dispatch. */}
            What an agent on this item is told. A run in flight re-reads it, so
            an edit here redirects it.
          </CardDescription>
        </div>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={editing ? "Preview" : "Edit"}
          onClick={() => setEditing((e) => !e)}
        >
          {editing ? <Eye className="size-4" /> : <Pencil className="size-4" />}
        </Button>
      </CardHeader>
      <CardContent>
        {collapsible ? (
          <details>
            <summary className="cursor-pointer select-none text-sm text-muted-foreground">
              Show instructions
            </summary>
            <div className="mt-3">{body}</div>
          </details>
        ) : (
          body
        )}
      </CardContent>
    </Card>
  );
}
