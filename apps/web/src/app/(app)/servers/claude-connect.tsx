"use client";

// US-52.3: connect this machine's Claude subscription from the browser.
// The app hosts the flow; the machine keeps the secret. Claude Code itself
// performs the OAuth (`claude setup-token`, run in the in-app terminal); the
// factory records only a connected date — never the token.

import { useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import { BadgeCheck, Loader2, Terminal, Unplug } from "lucide-react";

import { apiCall, ApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { ConfirmDialog } from "@/components/confirm-dialog";

function openTerminalPopup(serverId: string) {
  window.open(
    `/terminal/${serverId}`,
    `ssh_${serverId}`,
    "popup=yes,width=1024,height=720,location=no,menubar=no,toolbar=no,status=no,scrollbars=yes,resizable=yes",
  );
}

export function ClaudeConnectCard({
  serverId,
  connectedAt,
  factoryTokenSet,
}: {
  serverId: string;
  connectedAt: string | null;
  /** Whether the org holds a factory token (us-52.2) — it outranks this one. */
  factoryTokenSet: boolean;
}) {
  const router = useRouter();
  const [busy, setBusy] = useState<"prepare" | "verify" | "disconnect" | null>(
    null,
  );
  const [command, setCommand] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function prepare() {
    setBusy("prepare");
    setError(null);
    setMessage(null);
    try {
      const res = await apiCall(
        `/api/v1/servers/${serverId}/claude-subscription/prepare`,
        { method: "POST" },
      );
      setCommand(res.command as string);
    } catch (e) {
      setError(
        e instanceof ApiError ? e.message : (e as Error).message,
      );
    } finally {
      setBusy(null);
    }
  }

  async function verify() {
    setBusy("verify");
    setError(null);
    try {
      const res = await apiCall(
        `/api/v1/servers/${serverId}/claude-subscription/verify`,
        { method: "POST" },
      );
      if (res.connected) {
        setMessage(`Connected — the token is installed in ${res.slots} agent slot(s).`);
        setCommand(null);
      } else {
        setMessage(
          "No token found in the agent slots yet — run the script in the terminal first, then verify again.",
        );
      }
      router.refresh();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : (e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  async function disconnect() {
    setBusy("disconnect");
    setError(null);
    try {
      await apiCall(
        `/api/v1/servers/${serverId}/claude-subscription/disconnect`,
        { method: "POST" },
      );
      setMessage(null);
      router.refresh();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : (e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <BadgeCheck className="size-4 text-muted-foreground" />
          Claude subscription
        </CardTitle>
      </CardHeader>
      <CardContent className="grid gap-3 text-sm">
        {connectedAt ? (
          <div className="flex flex-wrap items-center gap-3">
            <span>
              Connected · {new Date(connectedAt).toLocaleDateString()}
            </span>
            {factoryTokenSet && (
              <span className="text-xs text-muted-foreground">
                Note: the org&apos;s factory-held token is set and outranks this
                machine&apos;s — remove it under Settings → LLM providers to
                bill this machine&apos;s account.
              </span>
            )}
            <ConfirmDialog
              trigger={
                <Button size="sm" variant="outline" disabled={busy !== null}>
                  {busy === "disconnect" ? (
                    <Loader2 className="size-4 animate-spin" />
                  ) : (
                    <Unplug className="size-4" />
                  )}
                  Disconnect
                </Button>
              }
              title="Disconnect the Claude subscription?"
              description="The token is removed from every agent slot on this machine and the agents restart. Subscription-mode runs then use the org's factory token if one is set, or fail naming the fix."
              confirmLabel="Disconnect"
              onConfirm={disconnect}
            />
          </div>
        ) : (
          <p className="text-muted-foreground">
            Not connected. Agents on this machine whose Claude billing is{" "}
            <span className="font-medium">Claude Code — OAuth</span> need a
            credential: connect a Claude account here (it stays on the
            machine), or set a factory token under Settings → LLM providers.
          </p>
        )}

        {!connectedAt && !command && (
          <Button
            size="sm"
            className="w-fit"
            onClick={prepare}
            disabled={busy !== null}
          >
            {busy === "prepare" && <Loader2 className="size-4 animate-spin" />}
            Connect Claude subscription
          </Button>
        )}

        {command && (
          <div className="grid gap-2 rounded-md border p-3">
            <p className="text-xs text-muted-foreground">
              The connect script is installed. In the terminal, run:
            </p>
            <code className="rounded bg-muted px-2 py-1 font-mono text-xs">
              {command}
            </code>
            <ol className="list-decimal space-y-1 pl-5 text-xs text-muted-foreground">
              <li>
                It starts <code>claude setup-token</code> — open the URL it
                prints in your own browser and sign in as yourself.
              </li>
              <li>
                Paste the code back into the CLI, then paste the printed{" "}
                <code>sk-ant-oat…</code> token when the script asks (input is
                hidden). The script installs it into every agent slot and
                restarts the agents — a run in flight here will be retried.
              </li>
              <li>Press Verify connection below.</li>
            </ol>
            <div className="flex gap-2">
              <Button
                size="sm"
                variant="outline"
                onClick={() => openTerminalPopup(serverId)}
              >
                <Terminal className="size-4" />
                Open terminal
              </Button>
              <Button size="sm" onClick={verify} disabled={busy !== null}>
                {busy === "verify" && (
                  <Loader2 className="size-4 animate-spin" />
                )}
                Verify connection
              </Button>
            </div>
          </div>
        )}

        {message && <p className="text-xs text-muted-foreground">{message}</p>}
        {error && (
          <p className="text-xs text-destructive" role="alert">
            {error}
          </p>
        )}
      </CardContent>
    </Card>
  );
}
