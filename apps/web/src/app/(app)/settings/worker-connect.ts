// apps/web/src/app/(app)/settings/worker-connect.ts
//
// US-3.3 / US-3.7 / US-3.14 — the single source of truth for
// worker-connection commands. The setup snippets AND the onboarding
// guides render from these definitions, so a snippet can't change
// without the guide following (the us-3.7 "no drift" rule).
//
// URLs are passed in: the MCP url is the single, unscoped /mcp endpoint
// (scope now lives on the worker token itself, not the URL) and the git
// remote is the slug-based factory remote — the caller resolves both
// from context.

import { API_URL } from "@/lib/api";

export const TOKEN_PLACEHOLDER = "<your-worker-token>";
export const BRANCH_CONVENTION = "factory/issue-<work-item-id>";
export const LEASES = { autonomous: "15 minutes", human: "24 hours" };

export type Snippet = { key: string; label: string; text: string };

export type SnippetUrls = {
  /** the single, unscoped MCP server URL */
  mcpUrl: string;
  /** the `git clone <url>` target (real remote, or a slug-placeholder one) */
  gitCloneUrl: string;
};

export function buildSnippets({ mcpUrl, gitCloneUrl }: SnippetUrls): Snippet[] {
  return [
    {
      key: "claude",
      label: "Claude Code",
      text: `claude mcp add --transport http factory ${mcpUrl} --header "X-Worker-Token: ${TOKEN_PLACEHOLDER}"`,
    },
    {
      key: "cursor",
      label: "Cursor — .cursor/mcp.json",
      text: `{
  "mcpServers": {
    "factory": {
      "url": "${mcpUrl}",
      "headers": { "X-Worker-Token": "${TOKEN_PLACEHOLDER}" }
    }
  }
}`,
    },
    {
      key: "opencode",
      label: "OpenCode — opencode.json",
      text: `{
  "mcp": {
    "factory": {
      "type": "remote",
      "url": "${mcpUrl}",
      "headers": { "X-Worker-Token": "${TOKEN_PLACEHOLDER}" }
    }
  }
}`,
    },
    {
      key: "grok",
      label: "Grok Build — ~/.grok/config.toml",
      text: `# ~/.grok/config.toml — Grok Build supports MCP servers
[mcp.factory]
url = "${mcpUrl}"
headers = { "X-Worker-Token" = "${TOKEN_PLACEHOLDER}" }`,
    },
    {
      key: "git",
      label: "Factory git remote (password = worker token)",
      text: `git clone ${gitCloneUrl}`,
    },
    {
      key: "runner",
      label: "Autonomous supervisor runner",
      text: `FACTORY_API_URL=${API_URL}
FACTORY_WORKER_TOKEN=${TOKEN_PLACEHOLDER}
python -m supervisor   # from apps/runner/ — modules & model are configured server-side`,
    },
    // US-13.9: the headless CLI worker path — a `claude -p` process per
    // run, no supervisor, no shell the factory controls.
    {
      key: "headless-config",
      label: "Headless worker — factory-mcp.json",
      text: `{
  "mcpServers": {
    "factory": {
      "type": "http",
      "url": "${mcpUrl}",
      "headers": { "X-Worker-Token": "${TOKEN_PLACEHOLDER}" }
    }
  }
}`,
    },
    {
      key: "headless-run",
      label: "Headless worker — invocation",
      text: `claude -p "Call list_available_work, claim the next item with claim_work, do the work per get_work_context, then submit." \\
  --mcp-config factory-mcp.json \\
  --strict-mcp-config \\
  --allowed-tools "mcp__factory" \\
  --output-format stream-json --verbose`,
    },
    {
      key: "headless-auth",
      label: "Headless worker — unattended auth",
      text: `claude setup-token   # one interactive login, prints a long-lived token
export CLAUDE_CODE_OAUTH_TOKEN=<paste-it>   # set wherever the worker runs`,
    },
    {
      key: "headless-check",
      label: "Headless worker — connection check",
      text: `curl -s -X POST ${mcpUrl} \\
  -H "Content-Type: application/json" \\
  -H "Accept: application/json, text/event-stream" \\
  -H "X-Worker-Token: ${TOKEN_PLACEHOLDER}" \\
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"list_available_work","arguments":{}}}'`,
    },
  ];
}

export function pickSnippet(snippets: Snippet[], key: string): Snippet {
  const found = snippets.find((s) => s.key === key);
  if (!found) throw new Error(`unknown snippet: ${key}`);
  return found;
}
