"use client";

// US-63.x: the token card, split out of MemberDetail so it can sit on the
// Connect tab instead of Details — a router token IS a connect credential,
// and having it a tab away from "how do I plug this in" was the split that
// never made sense. Same queries, same RPCs, same confirm wording as before.

import { useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import { Ban, Copy, Eye, Loader2, Plus, RefreshCw } from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import { cn } from "@/lib/utils";
import { formatLastSeen } from "@/lib/format-time";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { confirmDialog } from "@/components/ui/confirm-dialog";
import { principalName, type AgentSeat, type MemberRow, type WorkerRow } from "../team-view";

export function RouterTokenPanel({
  orgId,
  member,
  workers,
  canManageTokens,
  slot,
}: {
  orgId: string;
  member: MemberRow;
  workers: WorkerRow[];
  canManageTokens: boolean;
  /** US-27.9: the machine this agent runs on, when Build Mill owns it — only
   *  needed for the revoke warning's wording. */
  slot: AgentSeat | null;
}) {
  const router = useRouter();
  const isAgent = member.principals?.kind === "agent";
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reveal, setReveal] = useState<{ name: string; token: string } | null>(
    null
  );
  const [creating, setCreating] = useState(false);

  async function revealToken(w: WorkerRow) {
    setError(null);
    setBusyId(w.id);
    const supabase = createClient();
    try {
      const { data, error: e } = await supabase.rpc("reveal_worker_token", {
        p_worker: w.id,
      });
      if (e) setError(e.message);
      else if (typeof data === "string") setReveal({ name: w.name, token: data });
    } finally {
      setBusyId(null);
    }
  }

  async function regenerate(w: WorkerRow) {
    if (
      !(await confirmDialog({
        title: "Regenerate token?",
        description:
          "A fresh token is issued and the current one stops working immediately.",
        confirmLabel: "Regenerate",
        destructive: true,
      }))
    )
      return;
    setError(null);
    setBusyId(w.id);
    const supabase = createClient();
    try {
      const { data, error: e } = await supabase.rpc("regenerate_worker_token", {
        p_worker: w.id,
      });
      if (e) setError(e.message);
      else {
        if (typeof data === "string") setReveal({ name: w.name, token: data });
        router.refresh();
      }
    } finally {
      setBusyId(null);
    }
  }

  async function setNoClaimCheckout(w: WorkerRow, allow: boolean) {
    setError(null);
    setBusyId(w.id);
    const supabase = createClient();
    try {
      const { error: e } = await supabase
        .from("workers")
        .update({ no_claim_checkout: allow })
        .eq("id", w.id);
      if (e) setError(e.message);
      else router.refresh();
    } finally {
      setBusyId(null);
    }
  }

  async function revoke(w: WorkerRow) {
    if (
      !(await confirmDialog({
        title: slot ? "Revoke this managed agent's token?" : "Revoke token?",
        description: slot
          ? `This agent runs on ${slot.hostName} (slot ${slot.slotIndex}), and its ` +
            "token lives in a file Build Mill wrote to that machine. Revoking it " +
            "leaves the machine running and unable to claim any work — there is " +
            "nobody there to paste a new token in. If you meant to rotate the " +
            "credential, use “Re-issue and push to the machine” on the host's " +
            "Agents tab instead; it mints a new token, writes it, and restarts " +
            "the service."
          : "It stops working immediately.",
        confirmLabel: "Revoke anyway",
        destructive: true,
      }))
    )
      return;
    setError(null);
    setBusyId(w.id);
    const supabase = createClient();
    try {
      const { error: e } = await supabase
        .from("workers")
        .update({ status: "revoked" })
        .eq("id", w.id)
        .eq("org_id", orgId);
      if (e) setError(e.message);
      else router.refresh();
    } finally {
      setBusyId(null);
    }
  }

  // Fallback only — every human is auto-provisioned a token by migration 096;
  // this restores one if it was ever fully removed.
  async function generateToken() {
    setError(null);
    setCreating(true);
    const supabase = createClient();
    try {
      const { data, error: rpcError } = await supabase.rpc("create_worker", {
        p_org: orgId,
        p_name: "Access token",
        p_type: "human",
        ...(member.user_id ? { p_user_id: member.user_id } : {}),
      });
      if (rpcError) {
        setError(rpcError.message);
        return;
      }
      const row = Array.isArray(data) ? data[0] : data;
      if (row?.token) setReveal({ name: "Access token", token: row.token });
      router.refresh();
    } finally {
      setCreating(false);
    }
  }

  if (!canManageTokens) {
    return (
      <p className="text-xs text-muted-foreground">
        Only {principalName(member)} or an admin can view this token.
      </p>
    );
  }

  return (
    <section className="grid gap-2">
      <h3 className="text-sm font-semibold">Router token</h3>
      <p className="text-xs text-muted-foreground">
        {isAgent
          ? "This agent authenticates every claim, push, and commit with this token."
          : "Your git & MCP password against the factory remote. Regenerate to rotate it."}
      </p>
      {workers.length === 0 ? (
        <Button
          variant="outline"
          size="sm"
          className="w-fit"
          disabled={creating}
          onClick={generateToken}
        >
          {creating ? (
            <Loader2 className="size-4 animate-spin" />
          ) : (
            <Plus className="size-4" />
          )}
          Generate token
        </Button>
      ) : (
        <ul className="grid gap-2">
          {workers.map((w) => (
            <li
              key={w.id}
              className={cn(
                "flex items-center justify-between gap-2 rounded-md border px-3 py-2 text-sm",
                w.status === "revoked" && "opacity-60"
              )}
            >
              <span className="flex min-w-0 flex-col gap-1">
                <span className="truncate font-medium">{w.name}</span>
                <span className="truncate text-xs text-muted-foreground">
                  …{w.token_last4} · Last used {formatLastSeen(w.last_seen_at)}
                </span>
                {/* us-110.1: the MCP project picker stood here. A worker's
                    projects are its access grants now — one list, edited on
                    the Projects tab, not a second scope narrowing it. */}
                <label className="flex cursor-pointer items-center gap-1.5 text-xs text-muted-foreground">
                  <Checkbox
                    checked={w.no_claim_checkout}
                    disabled={busyId === w.id}
                    onCheckedChange={(v) => void setNoClaimCheckout(w, v === true)}
                  />
                  No-claim checkout over MCP
                </label>
              </span>
              <span className="flex shrink-0 items-center gap-1">
                {w.status === "revoked" && (
                  <Badge variant="outline" className="text-muted-foreground">
                    Revoked
                  </Badge>
                )}
                <Button
                  variant="outline"
                  size="icon-sm"
                  title="Show token"
                  disabled={busyId === w.id}
                  onClick={() => revealToken(w)}
                >
                  {busyId === w.id ? (
                    <Loader2 className="size-3.5 animate-spin" />
                  ) : (
                    <Eye className="size-3.5" />
                  )}
                </Button>
                <Button
                  variant="outline"
                  size="icon-sm"
                  title="Regenerate"
                  disabled={busyId === w.id}
                  onClick={() => regenerate(w)}
                >
                  <RefreshCw className="size-3.5" />
                </Button>
                {w.status === "active" && (
                  <Button
                    variant="outline"
                    size="icon-sm"
                    title="Revoke"
                    disabled={busyId === w.id}
                    onClick={() => revoke(w)}
                  >
                    <Ban className="size-3.5" />
                  </Button>
                )}
              </span>
            </li>
          ))}
        </ul>
      )}

      {reveal && (
        <div className="grid gap-2 rounded-md border border-amber-500/40 bg-amber-500/5 p-3">
          <p className="text-sm font-medium">Token for {reveal.name}</p>
          <div className="flex min-w-0 items-center gap-2">
            <code className="min-w-0 flex-1 truncate rounded bg-muted px-2 py-1 font-mono text-xs">
              {reveal.token}
            </code>
            <Button
              variant="outline"
              size="sm"
              onClick={() => navigator.clipboard.writeText(reveal.token)}
            >
              <Copy className="size-4" />
            </Button>
          </div>
          <p className="text-xs text-muted-foreground">
            Use this as the git/MCP password against the factory remote.
          </p>
        </div>
      )}

      {error && <p className="text-sm font-medium text-destructive">{error}</p>}
    </section>
  );
}
