"use client";

import { useEffect, useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import { Building2, Check, ChevronsUpDown, Loader2 } from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import { cn } from "@/lib/utils";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import type { OrgOption } from "@/lib/active-org";

// US-9.7: switch the active org. Hidden for single-org users. Writes the
// selection to principals.active_org_id (own row, allowed by RLS) and reloads
// so every org-scoped page re-resolves.
export function OrgSwitcher({
  orgs,
  activeOrgId,
  collapsed = false,
  className,
}: {
  orgs: OrgOption[];
  activeOrgId: string | null;
  collapsed?: boolean;
  className?: string;
}) {
  const router = useRouter();
  /** The workspace being switched TO, while the switch is in flight. */
  const [pending, setPending] = useState<string | null>(null);
  const busy = pending !== null;

  // UAT: the switch writes `principals.active_org_id` and re-renders every
  // org-scoped page, which takes a moment. Clear the overlay when the server
  // actually comes back on the new workspace — not when the write returns,
  // which is a second or two too early and leaves the old data on screen
  // under a "done" UI.
  useEffect(() => {
    if (pending && activeOrgId === pending) setPending(null);
  }, [pending, activeOrgId]);

  // A switch that never lands must not leave the app behind a permanent
  // curtain: give up after 15s and let the manager try again.
  useEffect(() => {
    if (!pending) return;
    const t = setTimeout(() => setPending(null), 15_000);
    return () => clearTimeout(t);
  }, [pending]);

  if (orgs.length <= 1) return null;

  const active = orgs.find((o) => o.orgId === activeOrgId) ?? orgs[0];

  async function select(orgId: string) {
    if (orgId === activeOrgId) return;
    setPending(orgId);
    const supabase = createClient();
    const {
      data: { user },
    } = await supabase.auth.getUser();
    if (user) {
      await supabase
        .from("principals")
        .update({ active_org_id: orgId })
        .eq("auth_user_id", user.id);
    }
    router.refresh();
  }

  const pendingName = orgs.find((o) => o.orgId === pending)?.name;

  return (
    <>
      {/* UAT: say it out loud. The old feedback was a spinner on the trigger
          — inside a menu that has already closed — so a slow switch read as
          a dead click. */}
      {busy && (
        <div
          role="status"
          aria-live="polite"
          className="fixed inset-0 z-50 flex items-center justify-center bg-background/80 backdrop-blur-sm"
        >
          <div className="flex items-center gap-3 rounded-lg border bg-card px-5 py-4 shadow-lg">
            <Loader2 className="size-5 shrink-0 animate-spin text-muted-foreground" />
            <span className="text-sm font-medium">
              Switching workspace{pendingName ? ` to ${pendingName}` : ""}…
            </span>
          </div>
        </div>
      )}
      <DropdownMenu>
      <DropdownMenuTrigger
        render={
          <button
            type="button"
            title={active.name}
            className={cn(
              "flex items-center gap-2 rounded-md border px-2 py-1.5 text-sm transition-colors hover:bg-sidebar-accent/60",
              collapsed ? "justify-center" : "w-full",
              className,
            )}
          />
        }
      >
        {busy ? (
          <Loader2 className="size-4 shrink-0 animate-spin" />
        ) : (
          <Building2 className="size-4 shrink-0 text-muted-foreground" />
        )}
        {!collapsed && (
          <>
            <span className="min-w-0 flex-1 truncate text-left font-medium">{active.name}</span>
            <ChevronsUpDown className="size-3.5 shrink-0 text-muted-foreground" />
          </>
        )}
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="min-w-52">
        {orgs.map((o) => (
          <DropdownMenuItem key={o.orgId} onClick={() => select(o.orgId)}>
            <span className="flex-1 truncate">{o.name}</span>
            {o.orgId === active.orgId && <Check className="size-4" />}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
      </DropdownMenu>
    </>
  );
}
