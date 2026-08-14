"use client";

import { useEffect, useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import { Bell, Check, ChevronRight } from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import { cn } from "@/lib/utils";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  describeNotification,
  groupNotifications,
  notificationHref,
  type NotificationGroup,
} from "@/lib/notification-copy";

export type NotificationRow = {
  id: string;
  type: string;
  payload: Record<string, unknown>;
  read_at: string | null;
  created_at: string;
};

// US-91.15: the copy and the destinations live in `@/lib/notification-copy`,
// beside a test that pins every type the API actually writes. What used to be
// here was a verb map for three types nothing produces, and a deep link that
// required an `issue_id` the real payloads do not carry.

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
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set());
  const unread = items.filter((n) => !n.read_at).length;
  // US-91.15: the bell's job is to say something is wrong once, not seven
  // times. Repeats of the same fault from the same agent collapse to a count.
  const groups = groupNotifications(items);

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

  /** A row with somewhere to go navigates; a row without one expands in
   *  place. Clicking is never a silent no-op that only marks it read. */
  async function open(group: NotificationGroup<NotificationRow>) {
    const unread = group.all.filter((n) => !n.read_at).map((n) => n.id);
    if (unread.length) await markRead(unread);
    const href = notificationHref(group.head);
    if (href) {
      router.push(href);
      return;
    }
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(group.head.id)) next.delete(group.head.id);
      else next.add(group.head.id);
      return next;
    });
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
            groups.map((group) => {
              const n = group.head;
              const view = describeNotification(n);
              const href = notificationHref(n);
              const isOpen = expanded.has(n.id);
              return (
                <div key={n.id} className="border-b last:border-b-0">
                  <button
                    type="button"
                    onClick={() => open(group)}
                    className={cn(
                      "flex w-full flex-col gap-0.5 px-3 py-2 text-left text-sm hover:bg-accent/60",
                      group.unread > 0 && "bg-primary/5",
                    )}
                  >
                    <span className="flex items-center gap-1.5">
                      {group.unread > 0 && (
                        <span className="size-1.5 shrink-0 rounded-full bg-primary" />
                      )}
                      <span className="min-w-0 flex-1 truncate">
                        <span className="font-medium">{view.subject}</span>
                        {view.summary ? ` ${view.summary}` : ""}
                      </span>
                      {group.all.length > 1 && (
                        <span
                          className="shrink-0 rounded-full bg-muted px-1.5 text-[10px] font-semibold tabular-nums text-muted-foreground"
                          title={`${group.all.length} of these`}
                        >
                          ×{group.all.length}
                        </span>
                      )}
                      {!href && (
                        <ChevronRight
                          className={cn(
                            "size-3.5 shrink-0 text-muted-foreground transition-transform",
                            isOpen && "rotate-90",
                          )}
                        />
                      )}
                    </span>
                    {view.detail && (
                      <span
                        className="line-clamp-2 pl-3 text-xs text-muted-foreground"
                        title={view.detail}
                      >
                        {view.detail}
                      </span>
                    )}
                    <span className="pl-3 text-xs text-muted-foreground">
                      {relTime(n.created_at)}
                      {group.all.length > 1 &&
                        ` · oldest ${relTime(group.all[group.all.length - 1].created_at)}`}
                    </span>
                  </button>
                  {isOpen && !href && (
                    <div className="grid gap-1 bg-muted/40 px-3 pb-2 pl-6">
                      {group.all.map((one) => (
                        <span
                          key={one.id}
                          className="text-xs text-muted-foreground"
                        >
                          {relTime(one.created_at)}
                          {describeNotification(one).detail
                            ? ` — ${describeNotification(one).detail}`
                            : ""}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              );
            })
          )}
        </div>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
