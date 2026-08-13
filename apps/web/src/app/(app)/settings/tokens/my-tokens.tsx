"use client";

import { useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import { Ban, Check, Copy, Eye, Loader2, Plus, RefreshCw } from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import { formatLastSeen } from "@/lib/format-time";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { EmptyState } from "@/components/empty-state";
import { confirmDialog } from "@/components/ui/confirm-dialog";
import { KeyRound } from "lucide-react";

export type TokenRow = {
  id: string;
  name: string;
  token_last4: string;
  status: "active" | "revoked";
  last_seen_at: string | null;
  created_at: string;
};

export function MyTokens({
  orgId,
  canDevelop,
  tokens,
}: {
  orgId: string;
  canDevelop: boolean;
  tokens: TokenRow[];
}) {
  const router = useRouter();
  const [name, setName] = useState("");
  const [creating, setCreating] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reveal, setReveal] = useState<{ name: string; token: string } | null>(null);
  const [copied, setCopied] = useState(false);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (!name.trim()) return;
    setCreating(true);
    const supabase = createClient();
    try {
      const { data, error: rpcError } = await supabase.rpc("create_worker", {
        p_org: orgId,
        p_name: name.trim(),
        p_type: "human",
      });
      if (rpcError) {
        setError(rpcError.message);
        return;
      }
      const row = Array.isArray(data) ? data[0] : data;
      if (!row?.token) {
        setError("Token created but not returned — regenerate it.");
        return;
      }
      setReveal({ name: name.trim(), token: row.token });
      setCopied(false);
      setName("");
      router.refresh();
    } finally {
      setCreating(false);
    }
  }

  async function handleReveal(t: TokenRow) {
    setError(null);
    setBusyId(t.id);
    const supabase = createClient();
    try {
      const { data, error: rpcError } = await supabase.rpc("reveal_worker_token", {
        p_worker: t.id,
      });
      if (rpcError) {
        setError(rpcError.message);
        return;
      }
      if (typeof data === "string" && data) {
        setReveal({ name: t.name, token: data });
        setCopied(false);
      }
    } finally {
      setBusyId(null);
    }
  }

  async function handleRegenerate(t: TokenRow) {
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
    setBusyId(t.id);
    const supabase = createClient();
    try {
      const { data, error: rpcError } = await supabase.rpc("regenerate_worker_token", {
        p_worker: t.id,
      });
      if (rpcError) {
        setError(rpcError.message);
        return;
      }
      if (typeof data === "string" && data) {
        setReveal({ name: t.name, token: data });
        setCopied(false);
      }
      router.refresh();
    } finally {
      setBusyId(null);
    }
  }

  async function handleRevoke(t: TokenRow) {
    if (
      !(await confirmDialog({
        title: "Revoke token?",
        description: "It stops working immediately.",
        confirmLabel: "Revoke",
        destructive: true,
      }))
    )
      return;
    setError(null);
    setBusyId(t.id);
    const supabase = createClient();
    try {
      const { error: dbError } = await supabase
        .from("workers")
        .update({ status: "revoked" })
        .eq("id", t.id)
        .eq("org_id", orgId);
      if (dbError) {
        setError(dbError.message);
        return;
      }
      router.refresh();
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="grid gap-4">
      {tokens.length === 0 ? (
        <EmptyState
          icon={KeyRound}
          title="No tokens yet"
          description="Create a personal token to clone and push through the router."
        />
      ) : (
        <ul className="grid gap-2">
          {tokens.map((t) => (
            <li
              key={t.id}
              className={`flex items-center justify-between gap-3 rounded-md border px-3 py-2 text-sm ${
                t.status === "revoked" ? "opacity-60" : ""
              }`}
            >
              <span className="flex min-w-0 flex-col">
                <span className="truncate font-medium">{t.name}</span>
                <span className="truncate text-xs text-muted-foreground">
                  Token set · …{t.token_last4} · Last used {formatLastSeen(t.last_seen_at)}
                </span>
              </span>
              <span className="flex shrink-0 items-center gap-2">
                {t.status === "revoked" && (
                  <Badge variant="outline" className="text-muted-foreground">
                    Revoked
                  </Badge>
                )}
                <Button variant="outline" size="sm" disabled={busyId === t.id} onClick={() => handleReveal(t)}>
                  {busyId === t.id ? <Loader2 className="size-4 animate-spin" /> : <Eye className="size-4" />}
                  Show
                </Button>
                <Button variant="outline" size="sm" disabled={busyId === t.id} onClick={() => handleRegenerate(t)}>
                  <RefreshCw className="size-4" />
                </Button>
                {t.status === "active" && (
                  <Button variant="outline" size="sm" disabled={busyId === t.id} onClick={() => handleRevoke(t)}>
                    <Ban className="size-4" />
                  </Button>
                )}
              </span>
            </li>
          ))}
        </ul>
      )}

      {canDevelop ? (
        <form onSubmit={handleCreate} className="flex gap-2">
          <Input
            placeholder="Token name (e.g. Laptop — Cursor)"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <Button type="submit" disabled={creating || !name.trim()}>
            {creating ? <Loader2 className="size-4 animate-spin" /> : <Plus className="size-4" />}
            Create token
          </Button>
        </form>
      ) : (
        <p className="text-xs text-muted-foreground">
          Your role can&apos;t push through the router. Ask an admin for the
          Develop capability to create tokens.
        </p>
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
              onClick={async () => {
                await navigator.clipboard.writeText(reveal.token);
                setCopied(true);
              }}
            >
              {copied ? <Check className="size-4 text-green-600" /> : <Copy className="size-4" />}
            </Button>
          </div>
          <p className="text-xs text-muted-foreground">
            Use this as your git password against the factory remote.
          </p>
        </div>
      )}

      {error && <p className="text-sm font-medium text-destructive">{error}</p>}
    </div>
  );
}
