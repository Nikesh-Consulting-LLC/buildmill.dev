"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import {
  ExternalLink,
  Loader2,
  Maximize2,
  PencilRuler,
  RotateCcw,
} from "lucide-react";
import { apiCall, ApiError, apiFetchText } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { EmptyState } from "@/components/empty-state";
import { toastError, toastSuccess } from "@/components/ui/toast";

/** US-48.2: the screen an agent drew, before the story was planned.
 *
 * There is no gate here on purpose. A wireframe is a sketch, not a contract —
 * it is live the moment it lands — so the manager's whole control loop is
 * reading it and pressing Redo with a comment. That is also what keeps a
 * fifteen-story fan-out from putting fifteen approvals in Things to Do. */
export type WireframeState = {
  version: number | null;
  noUiSurface: boolean;
  reason: string | null;
  summary: string | null;
  screens: { name: string; route: string | null }[];
  inFlight: boolean;
  repoPath: string | null;
  repoUrl: string | null;
};

export function WireframePanel({
  issueId,
  state,
}: {
  issueId: string;
  state: WireframeState;
}) {
  const router = useRouter();
  const [html, setHtml] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [redoOpen, setRedoOpen] = useState(false);
  const [comment, setComment] = useState("");

  const drawn = state.version !== null && !state.noUiSurface;

  const load = useCallback(async () => {
    if (!drawn) return;
    setLoading(true);
    try {
      // The preview is fetched rather than framed by src: the endpoint needs
      // the session JWT, and an <iframe src> cannot carry one.
      setHtml(await apiFetchText(`/api/v1/issues/${issueId}/wireframe/preview`));
    } catch {
      setHtml(null);
    } finally {
      setLoading(false);
    }
  }, [drawn, issueId]);

  useEffect(() => {
    void load();
  }, [load, state.version]);

  async function draw(feedback?: string) {
    setBusy(true);
    try {
      await apiCall(`/api/v1/issues/${issueId}/wireframe/dispatch`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ feedback: feedback ?? null }),
      });
      toastSuccess(
        feedback
          ? "An agent will draw it again with your comment"
          : "An agent will read the repo and draw this story"
      );
      setRedoOpen(false);
      setComment("");
      router.refresh();
    } catch (e) {
      toastError(
        e instanceof ApiError
          ? String(e.message)
          : (e as Error).message || "Could not start it"
      );
    } finally {
      setBusy(false);
    }
  }

  function openFullScreen() {
    if (!html) return;
    // A blob URL rather than document.write: the preview is untrusted agent
    // output and must not run in the app's origin.
    const url = URL.createObjectURL(new Blob([html], { type: "text/html" }));
    window.open(url, "_blank", "noopener,noreferrer");
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
  }

  if (state.inFlight) {
    return (
      <div className="rounded-lg border p-8 text-center text-sm text-muted-foreground">
        <Loader2 className="mx-auto mb-2 size-5 animate-spin" />
        An agent is drawing this story.
      </div>
    );
  }

  if (state.noUiSurface) {
    return (
      <div className="flex flex-col gap-4">
        <div className="rounded-lg border p-6">
          <div className="text-sm font-medium">No user-visible surface</div>
          <p className="mt-1 text-sm text-muted-foreground">
            {state.reason ??
              "An agent read this story against the repository and found nothing a user sees."}
          </p>
          <p className="mt-3 text-xs text-muted-foreground">
            Nothing was written to the repository. That is the answer, not a
            failure — redraw it if you think there is a screen here.
          </p>
        </div>
        <div>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={busy}
            onClick={() => void draw()}
          >
            {busy ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <RotateCcw className="size-4" />
            )}
            Draw it anyway
          </Button>
        </div>
      </div>
    );
  }

  if (!drawn) {
    return (
      <EmptyState
        icon={PencilRuler}
        title="This story has not been drawn"
        description="An agent can sketch the screen against the app's existing pages — before a plan run spends real money designing it in prose."
        action={
          <Button type="button" disabled={busy} onClick={() => void draw()}>
            {busy ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <PencilRuler className="size-4" />
            )}
            Draw this story
          </Button>
        }
      />
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2">
        <div className="text-sm text-muted-foreground">
          {state.summary ?? "Drawn"}
          {state.version !== null ? ` · version ${state.version}` : ""}
        </div>
        <div className="ml-auto flex flex-wrap gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={openFullScreen}
            disabled={!html}
          >
            <Maximize2 className="size-4" />
            Full screen
          </Button>
          {state.repoUrl ? (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              render={
                <a
                  href={state.repoUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                />
              }
            >
              <ExternalLink className="size-4" />
              In the repo
            </Button>
          ) : null}
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => setRedoOpen((open) => !open)}
          >
            <RotateCcw className="size-4" />
            Redo
          </Button>
        </div>
      </div>

      {redoOpen ? (
        <div className="flex flex-col gap-2 rounded-lg border p-4">
          <label className="text-sm font-medium" htmlFor="wireframe-redo">
            What is wrong with it?
          </label>
          <Textarea
            id="wireframe-redo"
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            placeholder="The filter belongs in the page header, not in a card. The empty state should offer the primary action."
            rows={3}
          />
          <p className="text-xs text-muted-foreground">
            The agent gets this alongside the wireframe it is replacing, so the
            next attempt is informed rather than a fresh guess.
          </p>
          <div className="flex gap-2">
            <Button
              type="button"
              size="sm"
              disabled={busy || !comment.trim()}
              onClick={() => void draw(comment.trim())}
            >
              {busy ? <Loader2 className="size-4 animate-spin" /> : null}
              Draw it again
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => setRedoOpen(false)}
            >
              Cancel
            </Button>
          </div>
        </div>
      ) : null}

      <div className="overflow-hidden rounded-lg border">
        {loading ? (
          <div className="p-8 text-center text-sm text-muted-foreground">
            <Loader2 className="mx-auto mb-2 size-5 animate-spin" />
            Loading the wireframe…
          </div>
        ) : html ? (
          /* sandbox WITHOUT allow-same-origin: this is agent-authored markup
           * and script, and it must not be able to reach the app's origin,
           * cookies or session. allow-scripts alone is what the kit needs. */
          <iframe
            title="Wireframe"
            srcDoc={html}
            sandbox="allow-scripts"
            className="h-[70vh] w-full bg-background"
          />
        ) : (
          <div className="p-8 text-center text-sm text-muted-foreground">
            The wireframe could not be rendered.
          </div>
        )}
      </div>

      {state.repoPath ? (
        <p className="text-xs text-muted-foreground">
          Committed to <code>{state.repoPath}</code>. The agent that codes this
          story reads it from there.
        </p>
      ) : null}
    </div>
  );
}
