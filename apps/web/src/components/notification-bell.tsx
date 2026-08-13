"use client";

import { useEffect, useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import { Bell, Check } from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import { cn } from "@/lib/utils";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

export type NotificationRow = {
  id: string;
  type: string;
  payload: Record<string, unknown>;
  read_at: string | null;
  created_at: string;
};

const TYPE_VERB: Record<string, string> = {
  assigned: "assigned you",
  review_requested: "asked you to review",
  blocked: "is blocked",
};

function deepLink(n: NotificationRow): string | null {
  const issueId = n.payload.issue_id as string | undefined;
  if (!issueId) return null;
  return n.type === "review_requested" ? `/review/${issueId}` : `/issues/${issueId}`;
}

function relTime(iso: string) {
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

export function NotificationBell({
  principalId,
  orgId,
  initial,
}: {
  principalId: string;
  orgId: string;
  initial: NotificationRow[];
}) {
  const router = useRouter();
  const [items, setItems] = useState<NotificationRow[]>(initial);
  const unread = items.filter((n) => !n.read_at).length;

  useEffect(() => {
    setItems(initial);
  }, [initial]);

  useEffect(() => {
    if (!principalId) return;
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
        .channel(`notifications-${principalId}`)
        .on(
          "postgres_changes",
          {
            event: "INSERT",
            schema: "public",
            table: "notifications",
            filter: `recipient_id=eq.${principalId}`,
          },
          (payload) => setItems((prev) => [payload.new as NotificationRow, ...prev].slice(0, 40)),
        )
        .subscribe();
    }
    subscribe();
    return () => {
      cancelled = true;
      if (channel) supabase.removeChannel(channel);
    };
  }, [principalId]);

  async function markRead(ids: string[]) {
    if (ids.length === 0) return;
    setItems((prev) =>
      prev.map((n) => (ids.includes(n.id) ? { ...n, read_at: new Date().toISOString() } : n)),
    );
    const supabase = createClient();
    await supabase
      .from("notifications")
      .update({ read_at: new Date().toISOString() })
      .in("id", ids);
  }

  async function open(n: NotificationRow) {
    if (!n.read_at) await markRead([n.id]);
    const href = deepLink(n);
    if (href) router.push(href);
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={
          <button
            type="button"
            aria-label={`Notifications${unread ? ` (${unread} unread)` : ""}`}
            className="relative inline-flex size-8 items-center justify-center rounded-md hover:bg-sidebar-accent/60"
          />
        }
      >
        <Bell className="size-4" />
        {unread > 0 && (
          <span className="absolute -right-0.5 -top-0.5 min-w-4 rounded-full bg-primary px-1 text-center text-[10px] font-semibold leading-4 text-primary-foreground">
            {unread > 9 ? "9+" : unread}
          </span>
        )}
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-80 p-0">
        <div className="flex items-center justify-between border-b px-3 py-2">
          <span className="text-sm font-medium">Notifications</span>
          {unread > 0 && (
            <button
              type="button"
              className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
              onClick={() => markRead(items.filter((n) => !n.read_at).map((n) => n.id))}
            >
              <Check className="size-3.5" /> Mark all read
            </button>
          )}
        </div>
        <div className="max-h-96 overflow-y-auto">
          {items.length === 0 ? (
            <p className="px-3 py-6 text-center text-sm text-muted-foreground">
              You&apos;re all caught up.
            </p>
          ) : (
            items.map((n) => (
              <button
                key={n.id}
                type="button"
                onClick={() => open(n)}
                className={cn(
                  "flex w-full flex-col gap-0.5 border-b px-3 py-2 text-left text-sm last:border-b-0 hover:bg-accent/60",
                  !n.read_at && "bg-primary/5",
                )}
              >
                <span className="flex items-center gap-1.5">
                  {!n.read_at && <span className="size-1.5 shrink-0 rounded-full bg-primary" />}
                  <span className="truncate">
                    <span className="font-medium">{TYPE_VERB[n.type] ?? n.type}</span>
                    {": "}
                    {(n.payload.title as string) || "a work item"}
                  </span>
                </span>
                <span className="pl-3 text-xs text-muted-foreground">{relTime(n.created_at)}</span>
              </button>
            ))
          )}
        </div>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
