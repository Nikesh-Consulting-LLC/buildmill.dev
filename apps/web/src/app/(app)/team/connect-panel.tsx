"use client";

// US-73.1: the Connect tab is two things, not an essay. A person gets the MCP
// server and one credentialed git URL for the repo they pick; an agent gets
// the config for whichever tool it runs (Claude Code, Grok, Cursor, OpenCode,
// the supervisor runner) plus the same full git URL. The "Who is connecting"
// picker is gone — the panel always renders inside one member's row. The deep
// runner/headless reference lives on in the page-bottom Workers help.

import { useEffect, useMemo, useState } from "react";
import { Check, Copy, Loader2 } from "lucide-react";

import { createClient } from "@/lib/supabase/client";
import {
  emailAsGitUsername,
  factoryMcpUrl,
  factoryRemoteUrl,
  factoryRemoteUrlWithCreds,
} from "@/lib/factory-git";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { buildSnippets, pickSnippet, TOKEN_PLACEHOLDER } from "../settings/worker-connect";

export type ConnectPrincipal = {
  principalId: string;
  name: string;
  kind: "human" | "agent";
  workerId: string | null;
  /** US-73.1: doubles as the git username for a person (gitproxy ignores it). */
  email: string | null;
};
export type ConnectProject = { id: string; name: string; slug: string };

// Exported for the add-agent wizard (US-53.2), which shows the same runner
// block on its Done step.
export function CopyBlock({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="flex items-start gap-2">
      <pre className="min-w-0 flex-1 overflow-x-auto rounded-md border bg-muted px-3 py-2 font-mono text-xs whitespace-pre-wrap">
        {text}
      </pre>
      <Button
        variant="outline"
        size="sm"
        onClick={async () => {
          await navigator.clipboard.writeText(text);
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        }}
      >
        {copied ? <Check className="size-4 text-green-600" /> : <Copy className="size-4" />}
      </Button>
    </div>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="flex items-center gap-2">
      <span className="w-32 shrink-0 text-xs text-muted-foreground">{label}</span>
      <code className="min-w-0 flex-1 overflow-x-auto rounded-md border bg-muted px-3 py-1.5 font-mono text-xs">
        {value}
      </code>
      <Button
        variant="outline"
        size="sm"
        onClick={async () => {
          await navigator.clipboard.writeText(value);
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        }}
      >
        {copied ? <Check className="size-4 text-green-600" /> : <Copy className="size-4" />}
      </Button>
    </div>
  );
}

export function ConnectPanel({
  principals,
  projects,
  orgShortname,
  initialPrincipalId,
}: {
  principals: ConnectPrincipal[];
  projects: ConnectProject[];
  orgShortname: string;
  initialPrincipalId?: string | null;
}) {
  const [projId, setProjId] = useState(projects[0]?.id || "");
  const [token, setToken] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [tokenError, setTokenError] = useState<string | null>(null);

  const principal =
    principals.find((p) => p.principalId === initialPrincipalId) ??
    principals[0] ??
    null;
  const project = projects.find((p) => p.id === projId) ?? null;
  const workerId = principal?.workerId ?? null;

  // Reveal the selected principal's real token (RLS-gated to owners/self).
  useEffect(() => {
    setToken(null);
    setTokenError(null);
    if (!workerId) return;
    let cancelled = false;
    setLoading(true);
    createClient()
      .rpc("reveal_worker_token", { p_worker: workerId })
      .then(({ data, error }) => {
        if (cancelled) return;
        if (error) setTokenError(error.message);
        else if (typeof data === "string" && data) setToken(data);
        // A dangling vault reference answers null with no error (the dev DB
        // seed dropped vault rows); surface it instead of a silent placeholder.
        else setTokenError("no stored secret for this token");
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [workerId]);

  const slug = project?.slug || "<project-slug>";
  const tok = token || TOKEN_PLACEHOLDER;
  const isAgent = principal?.kind === "agent";

  // The full credentialed clone URL. A person's email is the username, with
  // `@` written as `.` (raw `@` breaks URL parsing) — gitproxy authenticates
  // on the token alone and ignores it, so it's purely a readable label;
  // agents (no email) stay "worker". Built with the URL API only when the
  // slug and token are real — placeholders like <project-slug> aren't valid
  // URL characters.
  const gitUsername =
    !isAgent && principal?.email ? emailAsGitUsername(principal.email) : "worker";
  const gitUrl =
    project && token
      ? factoryRemoteUrlWithCreds(orgShortname, slug, gitUsername, token)
      : `${factoryRemoteUrl(orgShortname, slug).replace(
          "://",
          `://${encodeURIComponent(gitUsername)}:${tok}@`,
        )}`;

  // Reuse the single snippet source (no drift), then substitute the real
  // token. us-110.1: the MCP URL is the same everywhere — a worker reaches
  // the projects its access grants name, and nothing on the token narrows
  // that; the repo picker chooses only the git remote.
  const snippets = useMemo(() => {
    const base = buildSnippets({
      mcpUrl: factoryMcpUrl(),
      gitCloneUrl: factoryRemoteUrl(orgShortname, slug),
    });
    return base.map((s) => ({ ...s, text: s.text.replaceAll(TOKEN_PLACEHOLDER, tok) }));
  }, [orgShortname, slug, tok]);

  const projectItems = projects.map((p) => ({ value: p.id, label: p.name }));

  return (
    <div className="grid gap-5">
      <label className="grid max-w-xs gap-1 text-sm">
        <span className="text-muted-foreground">Repository</span>
        <Select
          items={projectItems}
          value={projId}
          onValueChange={(v) => typeof v === "string" && setProjId(v)}
        >
          <SelectTrigger>
            <SelectValue placeholder="Select a repository" />
          </SelectTrigger>
          <SelectContent>
            {projects.map((p) => (
              <SelectItem key={p.id} value={p.id}>
                {p.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </label>

      {loading && (
        <p className="flex items-center gap-1 text-xs text-muted-foreground">
          <Loader2 className="size-3 animate-spin" /> revealing token…
        </p>
      )}
      {tokenError && (
        <p className="text-xs text-amber-600 dark:text-amber-400">
          Couldn&apos;t reveal this token — the values below show a placeholder.
          Regenerate it from the Router token card above, or ask an admin.
        </p>
      )}
      {!principal?.workerId && !loading && (
        <p className="text-xs text-muted-foreground">
          This member has no token yet — generate one from the Router token card
          above first.
        </p>
      )}

      {isAgent ? (
        <AgentConnect snippets={snippets} gitUrl={gitUrl} />
      ) : (
        <HumanConnect mcpUrl={factoryMcpUrl()} token={tok} gitUrl={gitUrl} />
      )}
    </div>
  );
}

// US-73.1: a person needs exactly two things — where MCP is, and one git URL
// they can paste whole.
function HumanConnect({
  mcpUrl,
  token,
  gitUrl,
}: {
  mcpUrl: string;
  token: string;
  gitUrl: string;
}) {
  return (
    <div className="grid gap-5">
      <section className="grid gap-2">
        <h3 className="text-sm font-semibold">MCP server</h3>
        <p className="text-xs text-muted-foreground">
          Point your coding tool at this server with the token as the{" "}
          <code className="font-mono">X-Worker-Token</code> header.
        </p>
        <Field label="URL" value={mcpUrl} />
        <Field label="X-Worker-Token" value={token} />
      </section>

      <section className="grid gap-2">
        <h3 className="text-sm font-semibold">Git access</h3>
        <p className="text-xs text-muted-foreground">
          Clone and push through the factory remote — credentials are already in
          the URL (your email is the username, with{" "}
          <code className="font-mono">@</code> written as{" "}
          <code className="font-mono">.</code>; the token is the password).
        </p>
        <Field label="Remote URL" value={gitUrl} />
      </section>
    </div>
  );
}

// US-73.1: an agent's connect view is the tool options — pick the tab for
// what it runs. Git remote carries the same full credentialed URL.
function AgentConnect({
  snippets,
  gitUrl,
}: {
  snippets: ReturnType<typeof buildSnippets>;
  gitUrl: string;
}) {
  return (
    <section className="grid gap-3">
      <Tabs defaultValue="claude">
        <TabsList>
          <TabsTrigger value="claude">Claude Code</TabsTrigger>
          <TabsTrigger value="grok">Grok</TabsTrigger>
          <TabsTrigger value="cursor">Cursor</TabsTrigger>
          <TabsTrigger value="opencode">OpenCode</TabsTrigger>
          <TabsTrigger value="runner">Supervisor runner</TabsTrigger>
          <TabsTrigger value="git">Git remote</TabsTrigger>
        </TabsList>
        <TabsContent value="claude" className="pt-3">
          <CopyBlock text={pickSnippet(snippets, "claude").text} />
        </TabsContent>
        <TabsContent value="grok" className="pt-3">
          <p className="mb-2 text-xs text-muted-foreground">
            Add to <code className="font-mono">~/.grok/config.toml</code>:
          </p>
          <CopyBlock text={pickSnippet(snippets, "grok").text} />
        </TabsContent>
        <TabsContent value="cursor" className="pt-3">
          <p className="mb-2 text-xs text-muted-foreground">
            Create <code className="font-mono">.cursor/mcp.json</code>:
          </p>
          <CopyBlock text={pickSnippet(snippets, "cursor").text} />
        </TabsContent>
        <TabsContent value="opencode" className="pt-3">
          <p className="mb-2 text-xs text-muted-foreground">
            Add to <code className="font-mono">opencode.json</code>:
          </p>
          <CopyBlock text={pickSnippet(snippets, "opencode").text} />
        </TabsContent>
        <TabsContent value="runner" className="grid gap-2 pt-3">
          <p className="text-xs text-muted-foreground">
            Run on any machine with Python 3.12+ (from{" "}
            <code className="font-mono">apps/runner/</code>) — modules, model,
            and policy are configured server-side on the agent&apos;s Settings
            page. The full reference is under Workers help below.
          </p>
          <CopyBlock text={pickSnippet(snippets, "runner").text} />
        </TabsContent>
        <TabsContent value="git" className="grid gap-2 pt-3">
          <p className="text-xs text-muted-foreground">
            Clone and push through the factory remote — credentials are already
            in the URL. Push <code className="font-mono">factory/issue-&lt;id&gt;</code>{" "}
            for a run this agent holds.
          </p>
          <Field label="Remote URL" value={gitUrl} />
        </TabsContent>
      </Tabs>
    </section>
  );
}
