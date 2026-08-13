"use client";

import { useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import { Check, Loader2, Plus, X } from "lucide-react";
import { apiCall, ApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

export type PickableServer = {
  id: string;
  name: string;
  host: string;
  username: string;
};

const MODULES = ["claude", "grok", "opencode"] as const;

type Check = { check: string; ok: boolean; detail: string };

/**
 * US-26.1: register a machine as an agent server.
 *
 * The credential path is the existing one: an inline machine is created
 * through POST /servers, so the password or key takes the route it always
 * has — browser → api → the private data bucket — and never touches any of
 * the agent tables.
 */
export function RegisterAgentServerDialog({
  orgId,
  servers,
  // US-57.1: /admin/machines reuses this exact dialog — a shared machine is
  // registered the same way an org's own machine is, plus a pool name and a
  // capacity. `shared`/`pool_name`/`capacity` are refused server-side (403,
  // and a DB trigger behind that) for anyone but the platform admin, so this
  // prop only controls whether the fields are OFFERED, not whether they take.
  adminPool = false,
}: {
  orgId: string;
  servers: PickableServer[];
  adminPool?: boolean;
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<"existing" | "new">(
    servers.length ? "existing" : "new"
  );
  const [serverId, setServerId] = useState(servers[0]?.id ?? "");

  // inline machine
  const [name, setName] = useState("");
  const [host, setHost] = useState("");
  const [port, setPort] = useState("22");
  const [username, setUsername] = useState("root");
  const [authMethod, setAuthMethod] = useState<"password" | "ssh_key">("password");
  const [password, setPassword] = useState("");
  const [privateKey, setPrivateKey] = useState("");
  const [passphrase, setPassphrase] = useState("");

  // agent config
  const [workdir, setWorkdir] = useState("/opt/buildmill");
  const [modules, setModules] = useState<string[]>(["claude"]);
  const [extras, setExtras] = useState("");
  const [setupCommands, setSetupCommands] = useState("");
  const [allowSudo, setAllowSudo] = useState(false);

  // US-57.1: pool identity, admin-only
  const [poolName, setPoolName] = useState("");
  const [capacity, setCapacity] = useState("2");

  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [checks, setChecks] = useState<Check[] | null>(null);

  function toggleModule(m: string) {
    setModules((cur) => (cur.includes(m) ? cur.filter((x) => x !== m) : [...cur, m]));
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (adminPool && !poolName.trim()) {
      setError("A pool needs a name.");
      return;
    }
    setSaving(true);
    setError(null);
    setChecks(null);
    try {
      let id = serverId;
      if (mode === "new") {
        const payload: Record<string, unknown> = {
          org_id: orgId,
          name: name.trim(),
          host: host.trim(),
          port: Number(port) || 22,
          username: username.trim(),
          auth_method: authMethod,
          ...(authMethod === "password"
            ? { password }
            : { private_key: privateKey, passphrase: passphrase || null }),
        };
        const created = await apiCall("/api/v1/servers", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        id = created?.id ?? created?.server?.id;
        if (!id) throw new Error("The server was created but returned no id.");
      }

      await apiCall("/api/v1/agent-servers", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          server_id: id,
          workdir: workdir.trim(),
          modules,
          extra_packages: extras
            .split(/[\s,]+/)
            .map((p) => p.trim())
            .filter(Boolean),
          setup_commands: setupCommands,
          allow_agent_sudo: allowSudo,
          ...(adminPool
            ? { shared: true, pool_name: poolName.trim(), capacity: Number(capacity) || 0 }
            : {}),
        }),
      });

      setOpen(false);
      router.refresh();
    } catch (e) {
      // US-26.1: preflight failures come back as a list of named checks, so
      // "this machine is not Debian-family" reaches the operator as itself
      // and not as a generic failure.
      if (e instanceof ApiError && e.detail && typeof e.detail === "object" && "checks" in e.detail) {
        const payload = e.detail as { message?: string; checks: Check[] };
        setChecks(payload.checks);
        setError(payload.message ?? "This machine cannot host agents yet.");
      } else {
        setError((e as Error).message);
      }
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger render={<Button variant="create" />}>
        <Plus className="size-4" />
        {adminPool ? "Register a pool" : "Set up coding agents"}
      </DialogTrigger>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{adminPool ? "Register a pool" : "Set up coding agents"}</DialogTitle>
          <DialogDescription>
            A Linux machine (Debian or Ubuntu, with systemd) whose SSH user can
            sudo, and which can reach this factory over the network. Build Mill
            checks all of that from the machine itself before it saves
            anything.
            {adminPool &&
              " Named and sized here, this machine becomes an agent pool every org can place agents on."}
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={handleSubmit} className="grid max-h-[70vh] gap-4 overflow-y-auto pr-1">
          {adminPool && (
            <div className="grid grid-cols-[1fr_8rem] gap-2 rounded-md border p-3">
              <div className="grid gap-2">
                <Label htmlFor="as-pool-name">Pool name</Label>
                <Input
                  id="as-pool-name"
                  placeholder="Alpha"
                  value={poolName}
                  onChange={(e) => setPoolName(e.target.value)}
                />
              </div>
              <div className="grid gap-2">
                <Label htmlFor="as-pool-capacity">Capacity</Label>
                <Input
                  id="as-pool-capacity"
                  inputMode="numeric"
                  value={capacity}
                  onChange={(e) => setCapacity(e.target.value)}
                />
              </div>
            </div>
          )}
          {servers.length > 0 && (
            <div className="inline-flex rounded-md border p-0.5">
              {(["existing", "new"] as const).map((m) => (
                <button
                  key={m}
                  type="button"
                  onClick={() => setMode(m)}
                  className={cn(
                    "flex-1 rounded px-3 py-1.5 text-sm font-medium transition-colors",
                    mode === m
                      ? "bg-secondary text-secondary-foreground"
                      : "text-muted-foreground hover:text-foreground"
                  )}
                >
                  {m === "existing" ? "A registered machine" : "A new machine"}
                </button>
              ))}
            </div>
          )}

          {mode === "existing" ? (
            <div className="grid gap-2">
              <Label htmlFor="as-server">Machine</Label>
              <select
                id="as-server"
                value={serverId}
                onChange={(e) => setServerId(e.target.value)}
                className="h-9 rounded-md border bg-transparent px-3 text-sm"
              >
                {servers.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name} — {s.username}@{s.host}
                  </option>
                ))}
              </select>
              <p className="text-xs text-muted-foreground">
                Only machines that don&apos;t already host agents are listed.
              </p>
            </div>
          ) : (
            <>
              <div className="grid gap-2">
                <Label htmlFor="as-name">Name</Label>
                <Input
                  id="as-name"
                  placeholder="bravo"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                />
              </div>
              <div className="grid grid-cols-[1fr_5rem] gap-2">
                <div className="grid gap-2">
                  <Label htmlFor="as-host">Host</Label>
                  <Input
                    id="as-host"
                    placeholder="10.0.0.5"
                    value={host}
                    onChange={(e) => setHost(e.target.value)}
                  />
                </div>
                <div className="grid gap-2">
                  <Label htmlFor="as-port">Port</Label>
                  <Input
                    id="as-port"
                    inputMode="numeric"
                    value={port}
                    onChange={(e) => setPort(e.target.value)}
                  />
                </div>
              </div>
              <div className="grid gap-2">
                <Label htmlFor="as-username">Username</Label>
                <Input
                  id="as-username"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                />
              </div>
              <div className="grid gap-2">
                <Label>Authentication</Label>
                <div className="inline-flex rounded-md border p-0.5">
                  {(["password", "ssh_key"] as const).map((m) => (
                    <button
                      key={m}
                      type="button"
                      onClick={() => setAuthMethod(m)}
                      className={cn(
                        "flex-1 rounded px-3 py-1.5 text-sm font-medium transition-colors",
                        authMethod === m
                          ? "bg-secondary text-secondary-foreground"
                          : "text-muted-foreground hover:text-foreground"
                      )}
                    >
                      {m === "password" ? "Password" : "SSH key"}
                    </button>
                  ))}
                </div>
              </div>
              {authMethod === "password" ? (
                <div className="grid gap-2">
                  <Label htmlFor="as-password">Password</Label>
                  <Input
                    id="as-password"
                    type="password"
                    autoComplete="new-password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                  />
                </div>
              ) : (
                <>
                  <div className="grid gap-2">
                    <Label htmlFor="as-key">Private key</Label>
                    <Textarea
                      id="as-key"
                      rows={4}
                      className="font-mono text-xs"
                      value={privateKey}
                      onChange={(e) => setPrivateKey(e.target.value)}
                    />
                  </div>
                  <div className="grid gap-2">
                    <Label htmlFor="as-passphrase">Passphrase (optional)</Label>
                    <Input
                      id="as-passphrase"
                      type="password"
                      value={passphrase}
                      onChange={(e) => setPassphrase(e.target.value)}
                    />
                  </div>
                </>
              )}
              <p className="text-xs text-muted-foreground">
                The credential is stored write-only — it is never shown again,
                and never reaches the agent machine.
              </p>
            </>
          )}

          <div className="grid gap-2">
            <Label htmlFor="as-workdir">Working folder</Label>
            <Input
              id="as-workdir"
              value={workdir}
              onChange={(e) => setWorkdir(e.target.value)}
            />
            <p className="text-xs text-muted-foreground">
              Where the supervisor, its virtualenv, and each agent&apos;s
              workspace live.
            </p>
          </div>

          <div className="grid gap-2">
            <Label>Coding agent CLIs</Label>
            <div className="flex flex-wrap gap-4">
              {MODULES.map((m) => (
                <label key={m} className="flex items-center gap-2 text-sm">
                  <Checkbox
                    checked={modules.includes(m)}
                    onCheckedChange={() => toggleModule(m)}
                  />
                  <span className="font-mono text-xs">{m}</span>
                </label>
              ))}
            </div>
          </div>

          <div className="grid gap-2">
            <Label htmlFor="as-extras">Extra packages (optional)</Label>
            <Input
              id="as-extras"
              placeholder="postgresql-client dotnet-sdk-8.0"
              value={extras}
              onChange={(e) => setExtras(e.target.value)}
            />
            <p className="text-xs text-muted-foreground">
              apt packages your projects need. Re-applied on every update.
            </p>
          </div>

          <div className="grid gap-2">
            <Label htmlFor="as-setup">Setup commands (optional)</Label>
            <Textarea
              id="as-setup"
              rows={3}
              className="font-mono text-xs"
              placeholder="curl -fsSL https://example.com/install.sh | bash"
              value={setupCommands}
              onChange={(e) => setSetupCommands(e.target.value)}
            />
            <p className="text-xs text-muted-foreground">
              Run as root on this machine, in order, exactly as written.
            </p>
          </div>

          <label className="flex items-start gap-2 text-sm">
            <Checkbox
              checked={allowSudo}
              onCheckedChange={(v) => setAllowSudo(v === true)}
            />
            <span>
              Let agents install packages themselves
              <span className="block text-xs text-muted-foreground">
                Off by default. On, the agent account gets passwordless sudo —
                it can then change the machine that audits it.
              </span>
            </span>
          </label>

          {checks && (
            <ul className="grid gap-1 rounded-md border p-3 text-xs">
              {checks.map((c) => (
                <li key={c.check} className="flex items-start gap-2">
                  {c.ok ? (
                    <Check className="mt-0.5 size-3.5 shrink-0 text-emerald-600" />
                  ) : (
                    <X className="mt-0.5 size-3.5 shrink-0 text-red-600" />
                  )}
                  <span className={cn(!c.ok && "text-red-600 dark:text-red-400")}>
                    <span className="font-mono">{c.check}</span> — {c.detail}
                  </span>
                </li>
              ))}
            </ul>
          )}
          {error && <p className="text-sm text-red-600 dark:text-red-400">{error}</p>}

          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={saving}>
              {saving && <Loader2 className="size-4 animate-spin" />}
              Register
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
