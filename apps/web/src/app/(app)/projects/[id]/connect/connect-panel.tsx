"use client";

// US-3.10: per-project Connect page content — MCP URL, factory git
// remote, per-tool snippets, and a worker token in hand via the us-3.1
// regenerate RPC. Tokens stay write-only: a stored token is never
// displayed; the just-minted plaintext lives in client state only and
// vanishes when the user leaves the page.

import { useState } from "react";
import { Loader2, RefreshCw } from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import {
  buildSnippets,
  pickSnippet,
  TOKEN_PLACEHOLDER,
} from "../../../settings/worker-connect";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { CopyButton } from "@/components/copy-button";
import { confirmDialog } from "@/components/ui/confirm-dialog";

export type ConnectWorker = {
  id: string;
  name: string;
  type: "autonomous" | "human";
  token_last4: string;
  status: "active" | "revoked";
};

const TOOL_SNIPPET_KEYS = ["claude", "cursor", "opencode"];

function UrlRow({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="grid gap-1">
      <span className="text-xs font-medium text-muted-foreground">{label}</span>
      <div className="flex items-center gap-1">
        <code
          className="min-w-0 flex-1 truncate rounded-md border bg-muted px-3 py-2 font-mono text-xs"
          title={value}
        >
          {value}
        </code>
        <CopyButton text={value} title={`Copy ${label}`} />
      </div>
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}

export function ConnectPanel({
  projectName,
  mcpUrl,
  remoteUrl,
  workers,
}: {
  projectName: string;
  mcpUrl: string;
  remoteUrl: string | null;
  workers: ConnectWorker[];
}) {
  // The only place the plaintext ever exists client-side; gone on unmount.
  const [minted, setMinted] = useState<{ worker: string; value: string } | null>(
    null
  );
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const active = workers.filter((w) => w.status === "active");

  // US-3.14: snippets render from the shared source with this project's
  // scoped MCP URL and factory remote filled in.
  const snippets = buildSnippets({
    mcpUrl,
    gitCloneUrl: remoteUrl ?? "<factory-remote-url>",
  });
  const gitSnippet = remoteUrl ? pickSnippet(snippets, "git") : null;

  async function regenerate(w: ConnectWorker) {
    if (
      !(await confirmDialog({
        title: "Regenerate token?",
        description: `A fresh token is issued for "${w.name}" and the current one stops working immediately.`,
        confirmLabel: "Regenerate",
        destructive: true,
      }))
    )
      return;
    setError(null);
    setBusyId(w.id);
    const supabase = createClient();
    try {
      const { data, error: rpcError } = await supabase.rpc(
        "regenerate_worker_token",
        { p_worker: w.id }
      );
      if (rpcError) {
        setError(rpcError.message);
        return;
      }
      if (typeof data === "string" && data) {
        setMinted({ worker: w.name, value: data });
      }
    } finally {
      setBusyId(null);
    }
  }

  // With a token freshly minted, the snippets render ready to paste.
  const fill = (text: string) =>
    minted ? text.replaceAll(TOKEN_PLACEHOLDER, minted.value) : text;

  return (
    <div className="grid gap-4">
      {/* us-5.27: agents lead — MCP URL + token is the whole setup; git
          is the second path, for git-native workers. Nothing removed. */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Agents — MCP only</CardTitle>
          <CardDescription>
            An agent needs exactly two things: this MCP URL and a worker
            token. No git required: the workspace and submission happen over
            MCP (get_workspace hands the code out, submit_changeset hands it
            back — the factory does the commit, push, and PR).
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4">
          <UrlRow
            label="Factory MCP server"
            value={mcpUrl}
            hint={`The one factory MCP server for every project — scope now comes from the worker's own token, not the URL. Assign a worker to ${projectName} in Settings → Workers so its token only sees this project's pool.`}
          />
          {snippets
            .filter((s) => TOOL_SNIPPET_KEYS.includes(s.key))
            .map((s) => (
              <div key={s.key} className="grid gap-1">
                <span className="text-xs font-medium text-muted-foreground">
                  {s.label}
                </span>
                <div className="flex items-start gap-1">
                  <pre className="min-w-0 flex-1 overflow-x-auto rounded-md border bg-muted px-3 py-2 font-mono text-xs">
                    {fill(s.text)}
                  </pre>
                  <CopyButton text={fill(s.text)} title={`Copy ${s.label} snippet`} />
                </div>
              </div>
            ))}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Worker token</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-2">
          {minted ? (
            <div className="grid gap-1">
              <div className="flex items-center gap-1">
                <code className="min-w-0 flex-1 truncate rounded-md border bg-muted px-3 py-2 font-mono text-xs">
                  {minted.value}
                </code>
                <CopyButton text={minted.value} title="Copy worker token" />
              </div>
              <p className="text-xs text-muted-foreground">
                Shown once for “{minted.worker}” — copy it now. The snippets
                below have it filled in. Afterwards only the last 4 characters
                remain visible.
              </p>
            </div>
          ) : (
            <>
              <p className="text-xs text-muted-foreground">
                Stored tokens can&apos;t be displayed — regenerate to get a
                fresh one in hand (the old token stops working).
              </p>
              {active.length === 0 ? (
                <p className="text-xs text-muted-foreground">
                  No active workers yet.
                </p>
              ) : (
                <ul className="grid gap-1">
                  {active.map((w) => (
                    <li
                      key={w.id}
                      className="flex items-center justify-between gap-2 text-sm"
                    >
                      <span className="min-w-0 truncate">
                        {w.name}
                        <span className="text-xs text-muted-foreground">
                          {" "}
                          · {w.type} · …{w.token_last4}
                        </span>
                      </span>
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={busyId === w.id}
                        onClick={() => regenerate(w)}
                        title="Issue a fresh token (the old one stops working)"
                      >
                        {busyId === w.id ? (
                          <Loader2 className="size-3.5 animate-spin" />
                        ) : (
                          <RefreshCw className="size-3.5" />
                        )}
                        Regenerate
                      </Button>
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}
          <p className="text-xs text-muted-foreground">
            Need a new worker?{" "}
            <a
              href="/settings/workers"
              className="underline underline-offset-4"
            >
              Create it in Settings → Workers
            </a>
            .
          </p>
          {error && (
            <p className="text-xs font-medium text-destructive">{error}</p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Git-native workers</CardTitle>
          <CardDescription>
            For humans in IDEs and tools that want a real checkout (or repos
            above the MCP snapshot ceiling): clone and push through the
            factory git remote — HTTP Basic, password = worker token. No
            GitHub credentials, no PR to open.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4">
          {remoteUrl && (
            <UrlRow
              label={`Factory git remote — ${projectName}`}
              value={remoteUrl}
              hint="Push the branch named in the run context, then submit — the factory opens the PR itself."
            />
          )}
          {gitSnippet && (
            <div className="grid gap-1">
              <span className="text-xs font-medium text-muted-foreground">
                Clone this project through the factory
              </span>
              <div className="flex items-center gap-1">
                <pre className="min-w-0 flex-1 overflow-x-auto rounded-md border bg-muted px-3 py-2 font-mono text-xs">
                  {gitSnippet.text}
                </pre>
                <CopyButton
                  text={gitSnippet.text}
                  title="Copy clone command"
                />
              </div>
            </div>
          )}
          {!remoteUrl && (
            <p className="text-xs text-muted-foreground">
              No git remote yet — link a GitHub repository to this project to
              enable the git-native path.
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
