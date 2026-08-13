"use client";

import { useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import {
  Archive,
  ArchiveRestore,
  FlaskConical,
  Loader2,
  Pencil,
  Plus,
  ShieldAlert,
} from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { EmptyState } from "@/components/empty-state";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";

export type SuiteRow = {
  id: string;
  name: string;
  layer: string;
  run_command: string;
  results_path: string;
  server_id: string | null;
  run_on_uat: boolean;
  run_on_prod: boolean;
  blocks_signoff: boolean;
  timeout_minutes: number;
  status: string;
};

export type SuiteServerOption = { id: string; name: string };

function SuiteDialog({
  orgId,
  projectId,
  servers,
  suite,
}: {
  orgId: string;
  projectId: string;
  servers: SuiteServerOption[];
  suite?: SuiteRow;
}) {
  const router = useRouter();
  const isEdit = !!suite;
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(suite?.name ?? "");
  const [layer, setLayer] = useState(suite?.layer ?? "api");
  const [runCommand, setRunCommand] = useState(suite?.run_command ?? "");
  const [resultsPath, setResultsPath] = useState(
    suite?.results_path ?? "test-results/junit.xml"
  );
  const [serverId, setServerId] = useState(suite?.server_id ?? "deployment");
  const [runOnUat, setRunOnUat] = useState(suite?.run_on_uat ?? true);
  const [runOnProd, setRunOnProd] = useState(suite?.run_on_prod ?? false);
  const [blocksSignoff, setBlocksSignoff] = useState(
    suite?.blocks_signoff ?? false
  );
  const [timeout, setTimeoutMinutes] = useState(
    String(suite?.timeout_minutes ?? 30)
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (!name.trim()) {
      setError("Name is required.");
      return;
    }
    if (!runCommand.trim()) {
      setError("The run command is what the pipeline executes — required.");
      return;
    }
    const minutes = Number(timeout);
    if (!Number.isInteger(minutes) || minutes < 1 || minutes > 720) {
      setError("Timeout must be 1–720 minutes.");
      return;
    }

    setSaving(true);
    const supabase = createClient();
    try {
      const values = {
        name: name.trim(),
        layer,
        run_command: runCommand,
        results_path: resultsPath.trim() || "test-results/junit.xml",
        server_id: serverId === "deployment" ? null : serverId,
        run_on_uat: runOnUat,
        run_on_prod: runOnProd,
        blocks_signoff: blocksSignoff,
        timeout_minutes: minutes,
      };
      const { error: dbError } = isEdit
        ? await supabase.from("test_suites").update(values).eq("id", suite.id)
        : await supabase
            .from("test_suites")
            .insert({ ...values, org_id: orgId, project_id: projectId });
      if (dbError) {
        setError(dbError.message);
        return;
      }
      setOpen(false);
      router.refresh();
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger
        render={
          isEdit ? (
            <Button variant="ghost" size="icon-sm" />
          ) : (
            <Button variant="create" />
          )
        }
      >
        {isEdit ? (
          <Pencil className="size-4" />
        ) : (
          <>
            <Plus className="size-4" />
            New suite
          </>
        )}
      </DialogTrigger>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{isEdit ? "Edit suite" : "New test suite"}</DialogTitle>
          <DialogDescription>
            A command that runs this project&apos;s specs against a deployed
            instance and writes JUnit XML. Declaring it changes nothing until a
            release runs it.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSave} className="grid gap-4">
          <div className="grid grid-cols-2 gap-3">
            <div className="grid gap-2">
              <Label htmlFor="suite-name">Name</Label>
              <Input
                id="suite-name"
                placeholder="api, e2e, smoke…"
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="suite-layer">Layer</Label>
              <Select
                items={[
                  { value: "api", label: "api — pytest & friends" },
                  { value: "browser", label: "browser — Playwright" },
                ]}
                value={layer}
                onValueChange={(v) => {
                  if (typeof v === "string") setLayer(v);
                }}
              >
                <SelectTrigger id="suite-layer" className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="api">api — pytest &amp; friends</SelectItem>
                  <SelectItem value="browser">browser — Playwright</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
          <div className="grid gap-2">
            <Label htmlFor="suite-cmd">Run command</Label>
            <Textarea
              id="suite-cmd"
              rows={3}
              className="font-mono text-xs"
              placeholder={
                "pip install -r requirements-dev.txt\npython -m pytest tests/api --junitxml=$SF_RESULTS_PATH"
              }
              value={runCommand}
              onChange={(e) => setRunCommand(e.target.value)}
            />
            <p className="text-xs text-muted-foreground">
              Runs under <code>/bin/sh -e</code> in a checkout of the pinned
              commit, with <code>SF_BASE_URL</code> (the deployment&apos;s URL),{" "}
              <code>SF_COMMIT_SHA</code>, <code>SF_RESULTS_PATH</code> and{" "}
              <code>SF_CACHE_DIR</code> exported, plus the project&apos;s build
              config values.
            </p>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="grid gap-2">
              <Label htmlFor="suite-results">Results path</Label>
              <Input
                id="suite-results"
                className="font-mono text-xs"
                value={resultsPath}
                onChange={(e) => setResultsPath(e.target.value)}
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="suite-timeout">Timeout (minutes)</Label>
              <Input
                id="suite-timeout"
                inputMode="numeric"
                value={timeout}
                onChange={(e) => setTimeoutMinutes(e.target.value)}
              />
            </div>
          </div>
          <div className="grid gap-2">
            <Label htmlFor="suite-server">Runs on</Label>
            <Select
              items={[
                {
                  value: "deployment",
                  label: "Target deployment's server (default)",
                },
                ...servers.map((s) => ({ value: s.id, label: s.name })),
              ]}
              value={serverId}
              onValueChange={(v) => {
                if (typeof v === "string") setServerId(v);
              }}
            >
              <SelectTrigger id="suite-server" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="deployment">
                  Target deployment&apos;s server (default)
                </SelectItem>
                {servers.map((s) => (
                  <SelectItem key={s.id} value={s.id}>
                    {s.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground">
              The deployment&apos;s own server is the one machine guaranteed to
              reach its URL. Pick a dedicated test box if you don&apos;t want
              browsers installed on it.
            </p>
          </div>
          <div className="grid gap-2.5">
            <label className="flex cursor-pointer items-center gap-2 text-sm">
              <Checkbox
                checked={runOnUat}
                onCheckedChange={(v) => setRunOnUat(v === true)}
              />
              Run on every release&apos;s UAT deploy
            </label>
            <label className="flex cursor-pointer items-center gap-2 text-sm">
              <Checkbox
                checked={blocksSignoff}
                onCheckedChange={(v) => setBlocksSignoff(v === true)}
              />
              <span>
                Block sign-off until this suite passes{" "}
                <span className="text-xs text-muted-foreground">
                  (off = advisory only)
                </span>
              </span>
            </label>
            <label className="flex cursor-pointer items-start gap-2 text-sm">
              <Checkbox
                className="mt-0.5"
                checked={runOnProd}
                onCheckedChange={(v) => setRunOnProd(v === true)}
              />
              <span>
                Run against production after go-live
                <span className="mt-0.5 flex items-start gap-1 text-xs text-amber-600 dark:text-amber-400">
                  <ShieldAlert className="mt-0.5 size-3.5 shrink-0" />
                  These tests hit live production data — they must be
                  read-only. The factory cannot verify that; you are asserting
                  it.
                </span>
              </span>
            </label>
          </div>
          {error && (
            <p className="text-sm font-medium text-destructive">{error}</p>
          )}
          <DialogFooter>
            <Button type="submit" disabled={saving}>
              {saving && <Loader2 className="size-4 animate-spin" />}
              {isEdit ? "Save changes" : "Create suite"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function PresubmitGateCard({
  projectId,
  initial,
}: {
  projectId: string;
  initial: string | null;
}) {
  const router = useRouter();
  const [command, setCommand] = useState(initial ?? "");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  async function save() {
    setSaving(true);
    const supabase = createClient();
    await supabase
      .from("projects")
      .update({ presubmit_test_command: command.trim() || null })
      .eq("id", projectId);
    setSaving(false);
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
    router.refresh();
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Pre-submit gate</CardTitle>
        <CardDescription>
          The <span className="font-medium">fast</span> test command a coding
          agent runs in its workspace before submitting — evidence lands on the
          review page. Keep it under a few minutes: a slow gate is a skipped
          gate. The full suite belongs above, where machine time is cheap.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex items-start gap-2">
        <Input
          className="font-mono text-xs"
          placeholder="cd apps/api && python -m pytest -q"
          value={command}
          onChange={(e) => setCommand(e.target.value)}
        />
        <Button onClick={save} disabled={saving} variant="outline">
          {saving ? <Loader2 className="size-4 animate-spin" /> : null}
          {saved ? "Saved" : "Save"}
        </Button>
      </CardContent>
    </Card>
  );
}

export type ModuleRow = {
  id: string;
  name: string;
  path_globs: string[];
};

function ModulesCard({
  orgId,
  projectId,
  modules,
}: {
  orgId: string;
  projectId: string;
  modules: ModuleRow[];
}) {
  const router = useRouter();
  const [name, setName] = useState("");
  const [globs, setGlobs] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function add() {
    if (!name.trim()) return;
    setBusy(true);
    setError(null);
    const supabase = createClient();
    const { error: dbError } = await supabase.from("project_modules").insert({
      org_id: orgId,
      project_id: projectId,
      name: name.trim(),
      path_globs: globs
        .split("\n")
        .map((g) => g.trim())
        .filter(Boolean),
    });
    setBusy(false);
    if (dbError) {
      setError(dbError.message);
      return;
    }
    setName("");
    setGlobs("");
    router.refresh();
  }

  async function remove(id: string) {
    const supabase = createClient();
    await supabase.from("project_modules").delete().eq("id", id);
    router.refresh();
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Modules</CardTitle>
        <CardDescription>
          Name the areas of this codebase once — a label and the path globs
          that belong to it. Each release then records which modules it
          touched and suggests matching manual regression cases. A
          suggestion engine, never a gate; automated suites always run whole.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {modules.length > 0 && (
          <ul className="grid gap-2">
            {modules.map((m) => (
              <li
                key={m.id}
                className="flex items-center gap-2 rounded-md border px-3 py-1.5 text-sm"
              >
                <span className="font-medium">{m.name}</span>
                <code className="min-w-0 flex-1 truncate text-xs text-muted-foreground">
                  {m.path_globs.join("  ")}
                </code>
                <Button
                  variant="ghost"
                  size="icon-sm"
                  title="Remove this module (cases tagged with it keep working, untagged)"
                  onClick={() => remove(m.id)}
                >
                  <Archive className="size-4" />
                </Button>
              </li>
            ))}
          </ul>
        )}
        <div className="flex flex-col gap-2 sm:flex-row sm:items-start">
          <Input
            className="sm:max-w-44"
            placeholder="Module name"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <Textarea
            rows={2}
            className="font-mono text-xs"
            placeholder={"apps/api/**\ninfra/supabase/**"}
            value={globs}
            onChange={(e) => setGlobs(e.target.value)}
          />
          <Button onClick={add} disabled={busy || !name.trim()} variant="outline">
            {busy ? <Loader2 className="size-4 animate-spin" /> : <Plus className="size-4" />}
            Add
          </Button>
        </div>
        {error && (
          <p className="text-sm font-medium text-destructive">{error}</p>
        )}
      </CardContent>
    </Card>
  );
}

export function SuitesTab({
  orgId,
  projectId,
  suites,
  servers,
  presubmitTestCommand,
  modules,
}: {
  orgId: string;
  projectId: string;
  suites: SuiteRow[];
  servers: SuiteServerOption[];
  presubmitTestCommand: string | null;
  modules: ModuleRow[];
}) {
  const router = useRouter();
  const [busyId, setBusyId] = useState<string | null>(null);
  const serverName = new Map(servers.map((s) => [s.id, s.name]));

  async function setStatus(id: string, next: "active" | "archived") {
    setBusyId(id);
    const supabase = createClient();
    await supabase.from("test_suites").update({ status: next }).eq("id", id);
    setBusyId(null);
    router.refresh();
  }

  return (
    <div className="flex flex-col gap-6">
    <Card>
      <CardHeader className="flex-row items-start justify-between">
        <div className="grid gap-1.5">
          <CardTitle className="text-base">Test suites</CardTitle>
          <CardDescription>
            Automated suites the factory runs against a deployed instance —
            pytest for the API layer, Playwright for the browser. Specs live in
            this project&apos;s repo and pin with each release&apos;s commit.
          </CardDescription>
        </div>
        <SuiteDialog orgId={orgId} projectId={projectId} servers={servers} />
      </CardHeader>
      <CardContent>
        {!suites.length ? (
          <EmptyState
            icon={FlaskConical}
            title="No suites declared"
            description="Declare one to give automated testing a place to start. Nothing runs until a release deploys to UAT."
          />
        ) : (
          <ul className="grid gap-3">
            {suites.map((s) => (
              <li
                key={s.id}
                className={cn(
                  "rounded-lg border p-4",
                  s.status === "archived" && "opacity-60"
                )}
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <p className="font-medium">{s.name}</p>
                    <div className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
                      <Badge variant="secondary">{s.layer}</Badge>
                      {s.run_on_uat && <Badge variant="outline">UAT</Badge>}
                      {s.run_on_prod && (
                        <Badge variant="outline">prod smoke</Badge>
                      )}
                      {s.blocks_signoff && (
                        <Badge variant="outline">gates sign-off</Badge>
                      )}
                      {s.status === "archived" && (
                        <Badge variant="outline">archived</Badge>
                      )}
                      <span>
                        · runs on{" "}
                        {s.server_id
                          ? (serverName.get(s.server_id) ?? "a named server")
                          : "the deployment's server"}
                        · {s.timeout_minutes} min limit
                      </span>
                    </div>
                    <pre className="mt-2 overflow-x-auto rounded-md bg-muted/50 p-2 text-xs leading-5 whitespace-pre-wrap">
                      {s.run_command}
                    </pre>
                  </div>
                  <div className="flex shrink-0 items-center gap-1">
                    <SuiteDialog
                      orgId={orgId}
                      projectId={projectId}
                      servers={servers}
                      suite={s}
                    />
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      disabled={busyId === s.id}
                      title={
                        s.status === "active"
                          ? "Archive — stop running, keep history"
                          : "Restore to active"
                      }
                      onClick={() =>
                        setStatus(
                          s.id,
                          s.status === "active" ? "archived" : "active"
                        )
                      }
                    >
                      {s.status === "active" ? (
                        <Archive className="size-4" />
                      ) : (
                        <ArchiveRestore className="size-4" />
                      )}
                    </Button>
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
    <PresubmitGateCard projectId={projectId} initial={presubmitTestCommand} />
    <ModulesCard orgId={orgId} projectId={projectId} modules={modules} />
    </div>
  );
}
