"use client";

// us-109.1: removing a member used to be an icon button on the Team row, one
// click away from Suspend and sharing its shape. It is the only irreversible
// action on that row, so it moved off it: an agent's is on its settings page,
// a person's inside their expanded detail. Both render this, so the confirm
// wording and the delete are one implementation rather than two that drift.

import { useState } from "react";
import { Loader2, UserMinus } from "lucide-react";

import { createClient } from "@/lib/supabase/client";
import { Button } from "@/components/ui/button";
import { confirmDialog } from "@/components/ui/confirm-dialog";

export function RemoveMember({
  orgId,
  principalId,
  name,
  isAgent,
  onRemoved,
  className,
}: {
  orgId: string;
  principalId: string;
  /** Shown in the confirm so the manager sees which one they are removing —
   *  agents are deliberately non-unique by name (US-32.2), but the page this
   *  renders on is already scoped to exactly one. */
  name: string;
  isAgent: boolean;
  /** Where to go once the row is gone — a page navigates away, an inline
   *  panel refreshes the list under it. */
  onRemoved: () => void;
  className?: string;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function remove() {
    if (
      !(await confirmDialog({
        title: isAgent ? "Remove this agent?" : "Remove member?",
        description: isAgent
          ? `${name} loses access to this org, its tokens are revoked, and its machine slot is freed.`
          : `${name} loses access to this org and their tokens are revoked.`,
        confirmLabel: "Remove",
        destructive: true,
      }))
    )
      return;
    setError(null);
    setBusy(true);
    const supabase = createClient();
    try {
      const { error: dbError } = await supabase
        .from("organization_members")
        .delete()
        .eq("org_id", orgId)
        .eq("principal_id", principalId);
      if (dbError) setError(dbError.message);
      else onRemoved();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={className}>
      <Button
        variant="outline"
        size="sm"
        disabled={busy}
        onClick={() => void remove()}
        className="border-destructive/40 text-destructive hover:bg-destructive/10 hover:text-destructive"
      >
        {busy ? (
          <Loader2 className="size-4 animate-spin" />
        ) : (
          <UserMinus className="size-4" />
        )}
        {isAgent ? "Remove agent" : "Remove member"}
      </Button>
      {error && (
        <p className="mt-2 text-xs font-medium text-destructive">{error}</p>
      )}
    </div>
  );
}
