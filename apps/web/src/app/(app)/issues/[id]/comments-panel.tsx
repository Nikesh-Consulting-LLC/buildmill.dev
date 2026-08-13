"use client";

import { useEffect, useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import { Bot, Loader2, MessageSquare, Send } from "lucide-react";
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
import { MarkdownEditor } from "@/components/markdown-editor";
import { MarkdownView } from "@/components/markdown-view";

export type CommentRow = {
  id: string;
  author_kind: string;
  author_user: string | null;
  author_worker: string | null;
  body: string;
  created_at: string;
};

function formatWhen(iso: string) {
  return new Date(iso).toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

/** US-5.12: the work item's flat, immutable comment thread — org members
 * post from here under RLS; agents post via the claim-guarded MCP
 * add_comment tool. New comments stream in live over Realtime. */
export function CommentsPanel({
  issueId,
  orgId,
  comments,
  actorNames,
  workerNames,
}: {
  issueId: string;
  orgId: string;
  comments: CommentRow[];
  actorNames: Record<string, string>;
  workerNames: Record<string, string>;
}) {
  const router = useRouter();
  const [body, setBody] = useState("");
  const [posting, setPosting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const supabase = createClient();
    let channel: ReturnType<typeof supabase.channel> | null = null;
    let cancelled = false;

    async function subscribe() {
      const {
        data: { session },
      } = await supabase.auth.getSession();
      if (!session || cancelled) return;
      await supabase.realtime.setAuth(session.access_token);
      channel = supabase
        .channel(`comments-${issueId}`, { config: { private: false } })
        .on(
          "postgres_changes",
          {
            event: "INSERT",
            schema: "public",
            table: "issue_comments",
            filter: `issue_id=eq.${issueId}`,
          },
          () => router.refreshSilently()
        )
        .subscribe();
    }

    subscribe();
    return () => {
      cancelled = true;
      if (channel) supabase.removeChannel(channel);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [issueId]);

  function authorLabel(c: CommentRow) {
    if (c.author_kind === "worker") {
      return (c.author_worker && workerNames[c.author_worker]) ?? "an agent";
    }
    return (c.author_user && actorNames[c.author_user]) ?? "a member";
  }

  async function post() {
    if (!body.trim()) return;
    setError(null);
    setPosting(true);
    const supabase = createClient();
    const {
      data: { user },
    } = await supabase.auth.getUser();
    if (!user) {
      setPosting(false);
      setError("Not signed in.");
      return;
    }
    const { data: inserted, error: dbError } = await supabase
      .from("issue_comments")
      .insert({
        org_id: orgId,
        issue_id: issueId,
        author_kind: "user",
        author_user: user.id,
        body: body.trim(),
      })
      .select("id")
      .single();
    if (dbError) {
      setPosting(false);
      setError(dbError.message);
      return;
    }
    await supabase.from("issue_events").insert({
      org_id: orgId,
      issue_id: issueId,
      type: "comment-added",
      payload: { comment_id: inserted?.id, author_kind: "user" },
    });
    setBody("");
    setPosting(false);
    router.refresh();
  }

  return (
    <Card id="comments">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <MessageSquare className="size-4 text-muted-foreground" />
          Comments
        </CardTitle>
        <CardDescription>
          The shared thread on this work item — you and the agents working
          it both read and write here.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {comments.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No comments yet.
          </p>
        ) : (
          <ul className="grid gap-3">
            {comments.map((c) => (
              <li key={c.id} className="rounded-md border p-3">
                <div className="mb-1.5 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                  <span className="font-medium text-foreground">
                    {authorLabel(c)}
                  </span>
                  {c.author_kind === "worker" && (
                    <Badge variant="secondary" className="gap-1 font-normal">
                      <Bot className="size-3" />
                      agent
                    </Badge>
                  )}
                  <span>{formatWhen(c.created_at)}</span>
                </div>
                <MarkdownView className="[&_p]:my-1.5">{c.body}</MarkdownView>
              </li>
            ))}
          </ul>
        )}

        <div className="grid gap-2">
          <MarkdownEditor
            rows={3}
            value={body}
            onChange={setBody}
            orgId={orgId}
            placeholder="Write a comment — markdown supported."
          />
          <div className="flex justify-end">
            <Button
              size="sm"
              disabled={posting || !body.trim()}
              onClick={post}
            >
              {posting ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <Send className="size-4" />
              )}
              Comment
            </Button>
          </div>
        </div>

        {error && (
          <p className="text-sm font-medium text-destructive">{error}</p>
        )}
      </CardContent>
    </Card>
  );
}
