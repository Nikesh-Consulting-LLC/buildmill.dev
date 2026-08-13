"use client";

// US-52.2: the factory-held Claude subscription token. The paste is the whole
// connect flow — the browser OAuth that produces the token belongs to Claude
// Code's own client (`claude setup-token`), so the app hosts instructions and
// stores the result write-only in Vault; it never runs the OAuth itself.

import { useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import { KeyRound, Loader2, Trash2 } from "lucide-react";

import { createClient } from "@/lib/supabase/client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { confirmDialog } from "@/components/ui/confirm-dialog";

export type ClaudeSubscription = {
  key_last4: string;
  set_at: string;
  expires_at: string;
};

const EXPIRY_WARN_DAYS = 30;

export function SubscriptionSection({
  orgId,
  subscription,
}: {
  orgId: string;
  subscription: ClaudeSubscription | null;
}) {
  const router = useRouter();
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const daysLeft = subscription
    ? Math.floor(
        (new Date(subscription.expires_at).getTime() - Date.now()) /
          86_400_000,
      )
    : null;

  async function save() {
    // Refused client-side by shape before it goes anywhere; the RPC refuses
    // it again server-side. An API key must not be stored under this name.
    if (!token.trim().startsWith("sk-ant-oat")) {
      setError(
        "That is not a subscription token — run `claude setup-token` and paste its sk-ant-oat… result. An API key belongs under LLM providers above.",
      );
      return;
    }
    setBusy(true);
    setError(null);
    const supabase = createClient();
    const { error: rpcError } = await supabase.rpc(
      "set_claude_subscription_token",
      { p_org: orgId, p_token: token.trim() },
    );
    setBusy(false);
    if (rpcError) {
      setError(rpcError.message);
      return;
    }
    setToken(""); // never keep the pasted value around
    router.refresh();
  }

  async function remove() {
    if (
      !(await confirmDialog({
        title: "Remove the subscription token?",
        description:
          "Subscription-mode Claude runs fall back to whatever credential each machine holds (or fail naming the fix). This does not revoke the token at Anthropic — do that on claude.ai if it leaked.",
        confirmLabel: "Remove",
        destructive: true,
      }))
    )
      return;
    setBusy(true);
    setError(null);
    const supabase = createClient();
    const { error: rpcError } = await supabase.rpc(
      "clear_claude_subscription_token",
      { p_org: orgId },
    );
    setBusy(false);
    if (rpcError) {
      setError(rpcError.message);
      return;
    }
    router.refresh();
  }

  return (
    <div className="grid gap-3 text-sm">
      {subscription ? (
        <div className="flex flex-wrap items-center gap-3">
          <KeyRound className="size-4 text-muted-foreground" aria-hidden />
          <span>
            Token set · …{subscription.key_last4} · expires{" "}
            {new Date(subscription.expires_at).toLocaleDateString()}
          </span>
          {daysLeft !== null && daysLeft <= EXPIRY_WARN_DAYS && (
            <span className="text-amber-700 dark:text-amber-400">
              {daysLeft <= 0
                ? "Expired — subscription runs will fail until a fresh token is pasted."
                : `Expires in ${daysLeft} day${daysLeft === 1 ? "" : "s"} — paste a fresh one soon.`}
            </span>
          )}
          <Button
            variant="outline"
            size="sm"
            onClick={remove}
            disabled={busy}
            aria-label="Remove the Claude subscription token"
          >
            {busy ? (
              <Loader2 className="size-4 animate-spin" aria-hidden />
            ) : (
              <Trash2 className="size-4" aria-hidden />
            )}
            Remove
          </Button>
        </div>
      ) : (
        <p className="text-muted-foreground">
          No token set. Agents whose Claude billing is{" "}
          <span className="font-medium">Claude Code — OAuth</span> will use
          whatever credential their own machine holds.
        </p>
      )}

      <ol className="list-decimal space-y-1 pl-5 text-xs text-muted-foreground">
        <li>
          On any machine where you are logged in to Claude, run{" "}
          <code className="rounded bg-muted px-1 py-0.5">
            claude setup-token
          </code>{" "}
          and complete the browser login as yourself.
        </li>
        <li>
          Paste the printed <code>sk-ant-oat…</code> token below. It lives one
          year and is stored write-only — only the last 4 characters stay
          readable here.
        </li>
      </ol>

      <div className="flex max-w-xl gap-2">
        <Input
          type="password"
          value={token}
          onChange={(e) => setToken(e.target.value)}
          placeholder="sk-ant-oat…"
          aria-label="Claude subscription token"
          autoComplete="off"
        />
        <Button onClick={save} disabled={busy || !token.trim()}>
          {busy && <Loader2 className="size-4 animate-spin" aria-hidden />}
          {subscription ? "Replace token" : "Save token"}
        </Button>
      </div>

      {error && (
        <p className="text-xs text-destructive" role="alert">
          {error}
        </p>
      )}

      <p className="text-xs text-muted-foreground">
        A subscription is a personal Claude account: connect your own, and
        prefer per-machine credentials (on the agent server itself) when
        different operators run different machines. When both exist, this
        token wins.
      </p>
    </div>
  );
}
