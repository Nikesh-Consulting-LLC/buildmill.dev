"use client";

import { useState } from "react";
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
}: {
  orgs: OrgOption[];
  activeOrgId: string | null;
  collapsed?: boolean;
}) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);

  if (orgs.length <= 1) return null;

  const active = orgs.find((o) => o.orgId === activeOrgId) ?? orgs[0];

  async function select(orgId: string) {
    if (orgId === activeOrgId) return;
    setBusy(true);
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
    setBusy(false);
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={
          <button
            type="button"
            title={active.name}
            className={cn(
              "flex items-center gap-2 rounded-md border px-2 py-1.5 text-sm transition-colors hover:bg-sidebar-accent/60",
              collapsed ? "justify-center" : "w-full",
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
  );
}
