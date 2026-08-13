"use client";

// US-34.1: the MCP server catalog.
//
// us-31.9 gave an agent one MCP server — the factory itself. An agent asked to
// implement a feature and verify it has no browser, no database client, no docs
// lookup, and nothing beyond a shell command: it writes code and asserts it
// works. Every useful MCP server is a tool it does not have.
//
// Most of them need a credential, and that is the whole difficulty. An agent
// machine holds exactly ONE kind of secret. So credentials are entered here,
// stored write-only in Vault, and never reach a machine — the agent gets a proxy
// URL and a key worth one run.

import { useCallback, useEffect, useState } from "react";

import { apiCall } from "@/lib/api";
import { createClient } from "@/lib/supabase/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { toastError, toastSuccess } from "@/components/ui/toast";
import { EmptyState } from "@/components/empty-state";
import { Wrench } from "lucide-react";

type Server = {
  id: string;
  name: string;
  slug: string;
  description: string;
  transport: "http" | "stdio";
  endpoint: string | null;
  command: string | null;
  declared_tools: string[];
  needs_credential: boolean;
  credential_header: string | null;
  key_last4: string | null;
  enabled: boolean;
  last_check_ok: boolean | null;
  last_check_error: string | null;
};

// US-9.7: `orgId` is resolved server-side (settings/tools/page.tsx via
// requireOrg) and this component is remounted with `key={orgId}` whenever
// the active workspace changes — so a stale org can never linger in state
// the way it would if this component re-derived and cached its own orgId.
export default function ToolsView({ orgId }: { orgId: string }) {
  const [servers, setServers] = useState<Server[] | null>(null);
  const [adding, setAdding] = useState(false);

  const load = useCallback(async () => {
    try {
      const res = await apiCall(`/api/v1/orgs/${orgId}/mcp-servers`);
      setServers(res?.servers ?? []);
    } catch {
      setServers([]);
    }
  }, [orgId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function remove(server: Server) {
    try {
      await apiCall(`/api/v1/mcp-servers/${server.id}`, { method: "DELETE" });
      toastSuccess("Removed", `${server.name} is no longer in the catalog.`);
      await load();
    } catch (e) {
      toastError("Could not remove", (e as Error).message);
    }
  }

  return (
    <div className="flex w-full flex-col gap-6">
      <div>
        <h2 className="text-lg font-semibold">Tool servers</h2>
        <p className="max-w-3xl text-sm text-muted-foreground">
          The MCP servers your agents may use. Registering one grants it to
          nobody — a preset (platform-managed since US-57.6) has to name it
          before any run can reach it, so adding a server here cannot change
          how existing runs behave. Credentials are stored write-only and
          never reach an agent machine: the agent gets a proxy URL and a key
          that dies with its run.
        </p>
      </div>

      {servers === null ? (
        <p className="text-sm text-muted-foreground">Loading the catalog…</p>
      ) : servers.length === 0 ? (
        <EmptyState
          icon={Wrench}
          title="No tool servers yet"
          description="An agent with only the factory's own tools can read the repo and hand work back, but it cannot open a browser, query a database or look anything up. Register a server to change that — Playwright needs no credential at all."
        />
      ) : (
        <div className="grid gap-3">
          {servers.map((s) => (
            <div key={s.id} className="rounded-lg border p-4 text-sm">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium">{s.name}</span>
                <Badge variant="outline" className="font-mono text-[11px]">
                  {s.slug}
                </Badge>
                <Badge variant="secondary" className="text-[11px]">
                  {s.transport}
                </Badge>
                {s.needs_credential ? (
                  <Badge variant="outline" className="text-[11px]">
                    {s.key_last4
                      ? `credential set · …${s.key_last4}`
                      : "credential needed"}
                  </Badge>
                ) : (
                  <Badge variant="outline" className="text-[11px]">
                    no credential
                  </Badge>
                )}
                {s.enabled ? (
                  <Badge className="bg-emerald-100 text-emerald-700 text-[11px] dark:bg-emerald-950 dark:text-emerald-300">
                    enabled
                  </Badge>
                ) : (
                  <Badge className="bg-amber-100 text-amber-700 text-[11px] dark:bg-amber-950 dark:text-amber-300">
                    disabled
                  </Badge>
                )}
                <Button
                  variant="outline"
                  size="sm"
                  className="ml-auto"
                  onClick={() => void remove(s)}
                >
                  Remove
                </Button>
              </div>
              {s.description && (
                <p className="mt-1 text-xs text-muted-foreground">
                  {s.description}
                </p>
              )}
              <p className="mt-1 font-mono text-xs text-muted-foreground">
                {s.endpoint || s.command}
              </p>
              {s.declared_tools.length > 0 && (
                <p className="mt-1 text-xs text-muted-foreground">
                  Tools: {s.declared_tools.join(", ")}
                </p>
              )}
              {s.last_check_ok === false && (
                <p className="mt-1 text-xs text-red-600 dark:text-red-400">
                  Last check failed: {s.last_check_error}. It is disabled, so no
                  run can be granted it until it answers.
                </p>
              )}
              {s.transport === "stdio" && (
                <p className="mt-1 text-xs text-muted-foreground">
                  Runs on the agent machine, so it does not pass through the
                  factory&apos;s proxy — its tool calls are not recorded.
                </p>
              )}
            </div>
          ))}
        </div>
      )}

      {adding ? (
        <NewServer
          orgId={orgId}
          onDone={async () => {
            setAdding(false);
            await load();
          }}
          onCancel={() => setAdding(false)}
        />
      ) : (
        <div>
          <Button variant="outline" size="sm" onClick={() => setAdding(true)}>
            Register a tool server
          </Button>
        </div>
      )}
    </div>
  );
}

function NewServer({
  orgId,
  onDone,
  onCancel,
}: {
  orgId: string;
  onDone: () => Promise<void>;
  onCancel: () => void;
}) {
  const [name, setName] = useState("");
  const [transport, setTransport] = useState<"http" | "stdio">("http");
  const [endpoint, setEndpoint] = useState("");
  const [command, setCommand] = useState("");
  const [tools, setTools] = useState("");
  const [needsCredential, setNeedsCredential] = useState(false);
  const [header, setHeader] = useState("Authorization");
  const [credential, setCredential] = useState("");
  const [busy, setBusy] = useState(false);

  async function create() {
    setBusy(true);
    try {
      const res = await apiCall(`/api/v1/orgs/${orgId}/mcp-servers`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name,
          transport,
          endpoint: transport === "http" ? endpoint : null,
          command: transport === "stdio" ? command : null,
          declared_tools: tools
            .split(/[\s,]+/)
            .map((t) => t.trim())
            .filter(Boolean),
          needs_credential: needsCredential,
          credential_header: needsCredential ? header : null,
        }),
      });
      const serverId = res?.server?.id as string | undefined;

      // US-34.1: the credential goes from this browser STRAIGHT to Vault through
      // the membership-gated RPC — the same path `set_llm_provider_key` has
      // always used. It never travels through the factory's API, so it cannot
      // appear in an API log or a traceback.
      let check = res;
      if (needsCredential && serverId) {
        const supabase = createClient();
        const { error } = await supabase.rpc("set_mcp_server_key", {
          p_server: serverId,
          p_key: credential,
        });
        if (error) {
          toastError("Credential refused", error.message);
          await onDone();
          return;
        }
        // Now it can actually be reached — ask the factory to check.
        check = await apiCall(`/api/v1/mcp-servers/${serverId}/validate`, {
          method: "POST",
        });
      }

      if (check?.check_ok === false) {
        toastError(
          "Registered, but it did not answer",
          `${check.check_error} — it is disabled until it does, so no run can be granted it.`,
        );
      } else {
        toastSuccess(
          "Registered",
          "Name it in a preset to grant it to runs. Until then no agent can reach it.",
        );
      }
      await onDone();
    } catch (e) {
      toastError("Could not register", (e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid gap-3 rounded-lg border p-4 text-sm">
      <span className="font-medium">Register a tool server</span>
      <div className="grid gap-3 md:grid-cols-2">
        <label className="grid gap-1">
          <span className="text-xs text-muted-foreground">Name</span>
          <input
            value={name}
            maxLength={60}
            autoComplete="off"
            data-1p-ignore="true"
            data-lpignore="true"
            onChange={(e) => setName(e.target.value)}
            placeholder="Playwright"
            className="rounded-md border bg-background px-2 py-1"
          />
        </label>
        <label className="grid gap-1">
          <span className="text-xs text-muted-foreground">Transport</span>
          <select
            value={transport}
            onChange={(e) => setTransport(e.target.value as "http" | "stdio")}
            className="rounded-md border bg-background px-2 py-1"
          >
            <option value="http">http — the factory proxies it</option>
            <option value="stdio">stdio — a command on the agent machine</option>
          </select>
        </label>
        {transport === "http" ? (
          <label className="grid gap-1 md:col-span-2">
            <span className="text-xs text-muted-foreground">Endpoint</span>
            <input
              value={endpoint}
              autoComplete="off"
              onChange={(e) => setEndpoint(e.target.value)}
              placeholder="https://mcp.example.com/mcp"
              className="rounded-md border bg-background px-2 py-1 font-mono text-xs"
            />
          </label>
        ) : (
          <label className="grid gap-1 md:col-span-2">
            <span className="text-xs text-muted-foreground">Command</span>
            <input
              value={command}
              autoComplete="off"
              onChange={(e) => setCommand(e.target.value)}
              placeholder="npx @playwright/mcp@latest"
              className="rounded-md border bg-background px-2 py-1 font-mono text-xs"
            />
            <span className="text-xs text-muted-foreground">
              It must already be installed on the agent machine — the factory does
              not install it, and a command that is not there is a tool that is
              not there.
            </span>
          </label>
        )}
        <label className="grid gap-1 md:col-span-2">
          <span className="text-xs text-muted-foreground">
            Tools it exposes (optional)
          </span>
          <input
            value={tools}
            autoComplete="off"
            onChange={(e) => setTools(e.target.value)}
            placeholder="browser_navigate browser_click browser_snapshot"
            className="rounded-md border bg-background px-2 py-1 font-mono text-xs"
          />
          <span className="text-xs text-muted-foreground">
            Declared rather than discovered, so a manager can see what granting it
            allows before granting it — and so the catalog still reads correctly
            when the server is down.
          </span>
        </label>
      </div>

      <label className="flex items-start gap-2">
        <input
          type="checkbox"
          className="mt-0.5"
          checked={needsCredential}
          onChange={(e) => setNeedsCredential(e.target.checked)}
        />
        <span>
          <span className="font-medium">It needs a credential</span>
          <span className="block text-xs text-muted-foreground">
            Stored write-only. It never reaches an agent machine — the factory
            presents it on the way past, and only a last-four ever comes back out
            of this page.
          </span>
        </span>
      </label>

      {needsCredential && (
        <div className="grid gap-3 md:grid-cols-2">
          <label className="grid gap-1">
            <span className="text-xs text-muted-foreground">
              Presented as which header
            </span>
            <input
              value={header}
              autoComplete="off"
              onChange={(e) => setHeader(e.target.value)}
              className="rounded-md border bg-background px-2 py-1 font-mono text-xs"
            />
          </label>
          <label className="grid gap-1">
            <span className="text-xs text-muted-foreground">Credential</span>
            <input
              type="password"
              value={credential}
              // US-31.4: nothing a password manager owns belongs in this field.
              autoComplete="new-password"
              data-1p-ignore="true"
              data-lpignore="true"
              onChange={(e) => setCredential(e.target.value)}
              className="rounded-md border bg-background px-2 py-1 font-mono text-xs"
            />
          </label>
        </div>
      )}

      <div className="flex gap-2">
        <Button
          size="sm"
          disabled={
            busy ||
            !name.trim() ||
            (transport === "http" ? !endpoint.trim() : !command.trim()) ||
            (needsCredential && !credential.trim())
          }
          onClick={() => void create()}
        >
          {busy ? "Checking…" : "Register"}
        </Button>
        <Button variant="outline" size="sm" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </div>
  );
}
