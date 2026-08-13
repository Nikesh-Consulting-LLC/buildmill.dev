"use client";

import { confirmDialog } from "@/components/ui/confirm-dialog";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "@/lib/router-with-progress";
import {
  CheckCircle2,
  ChevronDown,
  Circle,
  Loader2,
  Pencil,
  Plus,
  Sparkles,
} from "lucide-react";
import { apiCall, ApiError } from "@/lib/api";
import { createClient } from "@/lib/supabase/client";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { TablesInsert, TablesUpdate } from "@/lib/supabase/database.types";
import type { DeploymentRow, ServerOption } from "./deployments-tab";

type Branch = { name: string; commit_sha: string };

const SECTION_COUNT = 4;

/** US-7.2: light client validation of the Website — an absolute http(s) URL
 * whose host matches the chosen kind (IPv4 literal vs domain). Mirrors the
 * deployments_website_shape DB check. Returns an error string or null. */
export function validateWebsite(kind: string, rawUrl: string): string | null {
  const url = rawUrl.trim();
  if (!url) return null; // optional
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    return "Website must be an absolute URL (e.g. https://app.example.com).";
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    return "Website must use http or https.";
  }
  const host = parsed.hostname;
  const isIpv4 = /^(\d{1,3}\.){3}\d{1,3}$/.test(host);
  if (kind === "ip") {
    if (!isIpv4 || host.split(".").some((o) => Number(o) > 255)) {
      return "For an IP website, the host must be an IPv4 address.";
    }
  } else {
    if (isIpv4 || !/^([a-z0-9]([a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}$/i.test(host)) {
      return "For a domain website, the host must be a domain name.";
    }
  }
  return null;
}

/** Collapsible accordion section — defined outside the dialog component so
 * React keeps its identity across renders (inputs must not lose focus). */
function SectionShell({
  title,
  summary,
  open,
  locked,
  complete,
  onToggle,
  children,
}: {
  title: string;
  summary: string;
  open: boolean;
  locked: boolean;
  complete: boolean;
  onToggle: () => void;
  children: React.ReactNode;
}) {
  return (
    <div className={cn("rounded-md border", open && "border-ring/60")}>
      <button
        type="button"
        onClick={onToggle}
        disabled={locked}
        className={cn(
          "flex w-full items-center justify-between gap-3 px-3 py-2 text-left",
          locked && "cursor-not-allowed opacity-50"
        )}
      >
        <span className="flex shrink-0 items-center gap-2 text-sm font-medium">
          {complete ? (
            <CheckCircle2 className="size-4 text-emerald-600" />
          ) : (
            <Circle className="size-4 text-muted-foreground" />
          )}
          {title}
        </span>
        <span className="flex min-w-0 items-center gap-2">
          {!open && (
            <span className="truncate text-xs text-muted-foreground">{summary}</span>
          )}
          <ChevronDown
            className={cn(
              "size-4 shrink-0 text-muted-foreground transition-transform",
              open && "rotate-180"
            )}
          />
        </span>
      </button>
      {open && <div className="grid gap-4 border-t px-3 py-3">{children}</div>}
    </div>
  );
}

export function DeploymentDialog({
  orgId,
  projectId,
  repoFullName,
  uatBranch,
  productionBranch,
  servers,
  deployment,
  isOwner,
  llmConfigured,
}: {
  orgId: string;
  projectId: string;
  repoFullName: string;
  uatBranch: string | null;
  productionBranch: string | null;
  servers: ServerOption[];
  deployment?: DeploymentRow;
  isOwner: boolean;
  llmConfigured: boolean;
}) {
  const router = useRouter();
  const isEdit = !!deployment;

  const [open, setOpen] = useState(false);
  // Accordion state: which section is open, and how far the user has gotten.
  const [step, setStep] = useState(0);
  const [visited, setVisited] = useState(isEdit ? SECTION_COUNT - 1 : 0);

  // US-50.1: the kind is what a deployment IS — asked first, chosen once, and
  // never edited. A history half SSH transfer and half merge is a history that
  // no longer means one thing.
  const [kind, setKind] = useState(deployment?.kind ?? "factory");
  const isExternal = kind === "external";

  const [name, setName] = useState(deployment?.name ?? "");
  const [serverId, setServerId] = useState(deployment?.server_id ?? "");
  const [branch, setBranch] = useState(deployment?.branch ?? "");
  const [targetBranch, setTargetBranch] = useState(
    deployment?.target_branch ?? ""
  );
  const [targetFolder, setTargetFolder] = useState(deployment?.target_folder ?? "");
  const [script, setScript] = useState(deployment?.script ?? "");
  const [draftModel, setDraftModel] = useState<string | null>(null);
  // US-43.4: whether the draft had this project's Deployment and Release
  // section to work from, or drafted from the stack and convention.
  const [draftGrounded, setDraftGrounded] = useState<boolean | null>(null);
  const [generating, setGenerating] = useState(false);
  const [generateError, setGenerateError] = useState<string | null>(null);
  const [envVarNames, setEnvVarNames] = useState<string[]>([]);
  const [timeout, setTimeoutMinutes] = useState(
    String(deployment?.run_timeout_minutes ?? 30)
  );
  // US-2.9: optional environment classification (dev/uat/production) — feeds
  // the release records' per-environment status columns.
  const [environment, setEnvironment] = useState(deployment?.environment ?? "");
  // US-7.2: the public reachable Website for this environment — a domain or
  // an IP literal. Distinct from the internal health_check_url.
  const [websiteKind, setWebsiteKind] = useState(deployment?.website_kind ?? "domain");
  const [websiteUrl, setWebsiteUrl] = useState(deployment?.website_url ?? "");
  // US-1.39: new deployments default to releases; existing keep their value.
  const [strategy, setStrategy] = useState(deployment?.strategy ?? "releases");
  const [keepReleases, setKeepReleases] = useState(
    String(deployment?.keep_releases ?? 5)
  );
  // US-1.36: branch payload filters (zips ship as-is).
  const [sourceFolder, setSourceFolder] = useState(deployment?.source_folder ?? "");
  const [excludePatterns, setExcludePatterns] = useState(
    deployment?.exclude_patterns ?? ""
  );
  // US-1.40: optional post-deploy health check.
  const [healthUrl, setHealthUrl] = useState(deployment?.health_check_url ?? "");
  const [healthStatus, setHealthStatus] = useState(
    String(deployment?.health_check_expected_status ?? 200)
  );
  const [healthWindow, setHealthWindow] = useState(
    String(deployment?.health_check_window_seconds ?? 60)
  );
  const [healthDelay, setHealthDelay] = useState(
    String(deployment?.health_check_initial_delay_seconds ?? 0)
  );
  // US-1.41: owners only — the checkbox is hidden from members and RLS
  // rejects a member's write anyway.
  const [isProtected, setIsProtected] = useState(deployment?.protected ?? false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Live branch list via the GitHub App connection (US-1.19). A 404 means
  // no installation is connected — the form blocks with a prompt.
  const [branches, setBranches] = useState<Branch[] | null>(null);
  const [branchesError, setBranchesError] = useState<string | null>(null);
  const [noConnection, setNoConnection] = useState(false);

  useEffect(() => {
    if (!open || branches !== null) return;
    let cancelled = false;
    (async () => {
      try {
        const list = (await apiCall(
          `/api/v1/github/repos/${repoFullName}/branches`
        )) as Branch[];
        if (!cancelled) setBranches(list);
      } catch (e) {
        if (cancelled) return;
        if (e instanceof ApiError && e.status === 404) setNoConnection(true);
        else setBranchesError((e as Error).message);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open, branches, repoFullName]);

  // US-1.51: names only (values stay write-only on the detail page).
  useEffect(() => {
    if (!open) return;
    if (!deployment?.id) {
      setEnvVarNames([]);
      return;
    }
    let cancelled = false;
    (async () => {
      const supabase = createClient();
      const { data } = await supabase
        .from("deployment_env_vars")
        .select("name")
        .eq("deployment_id", deployment.id)
        .order("name", { ascending: true });
      if (!cancelled) setEnvVarNames((data ?? []).map((r) => r.name));
    })();
    return () => {
      cancelled = true;
    };
  }, [open, deployment?.id]);

  async function handleGenerateScript() {
    if (!llmConfigured || generating) return;
    if (
      script.trim() &&
      !(await confirmDialog({
        title: "Replace the script?",
        description:
          "The current deployment script is replaced with an AI-generated draft.",
        confirmLabel: "Replace",
      }))
    ) {
      return;
    }
    setGenerateError(null);
    setGenerating(true);
    try {
      const result = (await apiCall("/api/v1/llm/generate-deploy-script", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          project_id: projectId,
          name: name.trim(),
          branch,
          target_folder: targetFolder.trim(),
          source_folder: sourceFolder.trim(),
          strategy,
          keep_releases: Number(keepReleases) || 5,
          run_timeout_minutes: Number(timeout) || 30,
          health_check_url: healthUrl.trim() || null,
          env_var_names: envVarNames,
        }),
      })) as {
        script: string;
        model: string;
        grounded_in_deployment_section?: boolean;
      };
      setScript(result.script);
      setDraftModel(result.model);
      setDraftGrounded(result.grounded_in_deployment_section ?? null);
    } catch (e) {
      setGenerateError(
        e instanceof ApiError
          ? String(e.message)
          : (e as Error).message || "Generation failed."
      );
    } finally {
      setGenerating(false);
    }
  }

  const selectedServer = servers.find((s) => s.id === serverId);
  const serverLabel = selectedServer
    ? `${selectedServer.name} (${selectedServer.host})`
    : "no machine";

  // US-7.3: a classified UAT/Production deployment releases from the project's
  // release branch (the single source of truth). When that branch is set, the
  // dialog inherits it (read-only) instead of an independent pick. Unset ⇒
  // the deployment keeps its own branch dropdown (back-compat).
  const inheritedBranch =
    environment === "uat"
      ? uatBranch?.trim() || null
      : environment === "production"
      ? productionBranch?.trim() || null
      : null;

  function validateSection(i: number): string | null {
    if (i === 0) {
      if (!name.trim()) return "Name is required.";
      if (!serverId) return "Pick a target machine.";
      const keepNum = Number(keepReleases);
      if (
        strategy === "releases" &&
        (!Number.isInteger(keepNum) || keepNum < 1 || keepNum > 50)
      ) {
        return "Releases to keep must be between 1 and 50.";
      }
      const websiteProblem = validateWebsite(websiteKind, websiteUrl);
      if (websiteProblem) return websiteProblem;
    }
    if (i === 1) {
      if (!inheritedBranch && !branch) return "Pick a branch.";
      const folder = targetFolder.trim();
      if (!folder || !folder.startsWith("/")) {
        return "Target folder must be an absolute path (starting with /).";
      }
      const timeoutNum = Number(timeout);
      if (!Number.isInteger(timeoutNum) || timeoutNum < 1 || timeoutNum > 720) {
        return "Run timeout must be between 1 and 720 minutes.";
      }
    }
    if (i === 3 && healthUrl.trim()) {
      const expected = Number(healthStatus);
      if (!Number.isInteger(expected) || expected < 100 || expected > 599) {
        return "Expected status must be an HTTP status code (100–599).";
      }
      const windowNum = Number(healthWindow);
      if (!Number.isInteger(windowNum) || windowNum < 5 || windowNum > 600) {
        return "Health check window must be between 5 and 600 seconds.";
      }
      const delayNum = Number(healthDelay);
      if (!Number.isInteger(delayNum) || delayNum < 0 || delayNum > 120) {
        return "Initial delay must be between 0 and 120 seconds.";
      }
    }
    return null;
  }

  function sectionFields(i: number): TablesUpdate<"deployments"> {
    if (i === 0) {
      return {
        name: name.trim(),
        server_id: serverId,
        environment: environment || null,
        website_kind: websiteUrl.trim() ? websiteKind : null,
        website_url: websiteUrl.trim() || null,
        strategy,
        keep_releases: strategy === "releases" ? Number(keepReleases) : 5,
      };
    }
    if (i === 1) {
      return {
        branch: inheritedBranch ?? branch,
        target_folder: targetFolder.trim(),
        source_folder: sourceFolder.trim().replace(/^\/+|\/+$/g, ""),
        exclude_patterns: excludePatterns,
        run_timeout_minutes: Number(timeout),
      };
    }
    if (i === 2) return { script };
    return {
      health_check_url: healthUrl.trim(),
      health_check_expected_status: Number(healthStatus) || 200,
      health_check_window_seconds: Number(healthWindow) || 60,
      health_check_initial_delay_seconds: Number(healthDelay) || 0,
      ...(isOwner ? { protected: isProtected } : {}),
    };
  }

  function friendlyDbError(dbError: { code?: string; message: string }): string {
    return dbError.code === "23505"
      ? "A deployment with this name already exists on this project."
      : dbError.message;
  }

  /** Section footer button: validates, persists immediately in edit mode,
   * then collapses this section and opens the next. */
  async function saveSection(i: number) {
    const problem = validateSection(i);
    if (problem) {
      setError(problem);
      return;
    }
    setError(null);
    if (isEdit) {
      setSaving(true);
      const supabase = createClient();
      const { error: dbError } = await supabase
        .from("deployments")
        .update(sectionFields(i))
        .eq("id", deployment!.id);
      setSaving(false);
      if (dbError) {
        setError(friendlyDbError(dbError));
        return;
      }
      router.refresh();
    }
    setVisited((v) => Math.max(v, i + 1));
    setStep(i + 1);
  }

  function toggleSection(i: number) {
    if (i > visited) return;
    setError(null);
    setStep((s) => (s === i ? -1 : i));
  }

  /** US-50.1: an external deployment's whole form. No strategy, no folders,
   * no exclude patterns, no keep-releases, no script, no health check — three
   * of the four factory sections are about moving files onto a box. */
  function validateExternal(): string | null {
    if (!name.trim()) return "Name is required.";
    if (!inheritedBranch && !branch) return "Pick a source branch.";
    if (!targetBranch.trim()) return "Pick the target branch.";
    if ((inheritedBranch ?? branch) === targetBranch.trim()) {
      return "The source and target branch must be different.";
    }
    return validateWebsite(websiteKind, websiteUrl);
  }

  function externalFields(): TablesUpdate<"deployments"> {
    return {
      name: name.trim(),
      branch: inheritedBranch ?? branch,
      target_branch: targetBranch.trim(),
      environment: environment || null,
      website_kind: websiteUrl.trim() ? websiteKind : null,
      website_url: websiteUrl.trim() || null,
      ...(isOwner ? { protected: isProtected } : {}),
    };
  }

  async function handleSaveExternal(e: React.FormEvent) {
    e.preventDefault();
    const problem = validateExternal();
    if (problem) {
      setError(problem);
      return;
    }
    setError(null);
    setSaving(true);
    try {
      const supabase = createClient();
      const { error: dbError } = isEdit
        ? await supabase
            .from("deployments")
            // `kind` is deliberately absent — editing never changes it.
            .update(externalFields())
            .eq("id", deployment!.id)
        : await supabase.from("deployments").insert({
            ...externalFields(),
            kind: "external",
            org_id: orgId,
            project_id: projectId,
          } as TablesInsert<"deployments">);
      if (dbError) {
        setError(friendlyDbError(dbError));
        return;
      }
      setOpen(false);
      router.refresh();
    } finally {
      setSaving(false);
    }
  }

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    for (let i = 0; i < SECTION_COUNT; i++) {
      const problem = validateSection(i);
      if (problem) {
        setError(problem);
        setStep(i);
        return;
      }
    }
    setError(null);
    setSaving(true);
    try {
      const supabase = createClient();
      const fields = {
        ...sectionFields(0),
        ...sectionFields(1),
        ...sectionFields(2),
        ...sectionFields(3),
      };
      const { error: dbError } = isEdit
        ? await supabase.from("deployments").update(fields).eq("id", deployment!.id)
        : await supabase.from("deployments").insert({
            ...fields,
            org_id: orgId,
            project_id: projectId,
          } as TablesInsert<"deployments">);
      if (dbError) {
        setError(friendlyDbError(dbError));
        return;
      }
      setDraftModel(null);
      setOpen(false);
      router.refresh();
    } finally {
      setSaving(false);
    }
  }

  const summaries = [
    `${name.trim() || "unnamed"} · ${serverLabel}` +
      (environment ? ` · ${environment}` : "") +
      (websiteUrl.trim() ? ` · ${websiteUrl.trim()}` : ""),
    branch
      ? `${branch}${sourceFolder.trim() ? ` /${sourceFolder.trim()}` : ""} → ${
          targetFolder.trim() || "…"
        } · ${timeout} min`
      : "not configured yet",
    script.trim()
      ? `${script.trim().split("\n").length} line(s)`
      : "transfer-only (no script)",
    (healthUrl.trim() ? `${healthUrl.trim()} (expect ${healthStatus})` : "no health check") +
      (isProtected ? " · protected" : ""),
  ];

  /** The repo's live branch list, as a dropdown — the same control the source
   * branch has always used, reused for the external target. */
  const branchSelect = (
    id: string,
    value: string,
    onChange: (v: string) => void,
    placeholder: string
  ) =>
    branchesError ? (
      <p className="text-sm font-medium text-destructive">
        Couldn&apos;t load branches: {branchesError}
      </p>
    ) : branches === null ? (
      <p className="inline-flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="size-3.5 animate-spin" />
        Loading branches…
      </p>
    ) : (
      <Select
        items={branches.map((b) => ({ value: b.name, label: b.name }))}
        value={value || null}
        onValueChange={(v) => {
          if (typeof v === "string") onChange(v);
        }}
      >
        <SelectTrigger id={id} className="w-full">
          <SelectValue placeholder={placeholder} />
        </SelectTrigger>
        <SelectContent>
          {branches.map((b) => (
            <SelectItem key={b.name} value={b.name}>
              {b.name}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    );

  /** US-50.1: asked first on creation; stated but not editable afterwards. */
  const kindPicker = isEdit ? (
    <p className="rounded-md border bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
      {isExternal ? (
        <>
          <span className="font-medium text-foreground">External</span> — this
          deployment ships by merging into{" "}
          <span className="font-mono">{targetBranch || "…"}</span>. A
          deployment&apos;s kind is fixed at creation.
        </>
      ) : (
        <>
          <span className="font-medium text-foreground">
            Deployed by this app
          </span>{" "}
          — files are transferred to a machine and a script runs there. A
          deployment&apos;s kind is fixed at creation.
        </>
      )}
    </p>
  ) : (
    <div className="grid gap-2">
      <Label htmlFor="dep-kind">How does this environment get deployed?</Label>
      <Select
        items={[
          { value: "factory", label: "By this app — files onto a machine" },
          { value: "external", label: "By something else — a merge on GitHub" },
        ]}
        value={kind}
        onValueChange={(v) => {
          if (typeof v === "string") {
            setKind(v);
            setError(null);
            setStep(0);
            setVisited(0);
          }
        }}
      >
        <SelectTrigger id="dep-kind" className="w-full">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="factory">
            By this app — files onto a machine
          </SelectItem>
          <SelectItem value="external">
            By something else — a merge on GitHub
          </SelectItem>
        </SelectContent>
      </Select>
      <p className="text-xs text-muted-foreground">
        {isExternal
          ? "Deploying means one thing: merging the source branch into the branch your pipeline watches. No machine, no transfer, no script — and the factory neither triggers nor watches what happens next."
          : "This app resolves the branch head, transfers the files to a machine over SSH, and runs the deployment script there."}{" "}
        This cannot be changed later.
      </p>
    </div>
  );

  const externalForm = (
    <form onSubmit={handleSaveExternal} className="grid gap-4">
      {kindPicker}

      <div className="grid gap-2">
        <Label htmlFor="dep-name">Name</Label>
        <Input
          id="dep-name"
          placeholder="production"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
      </div>

      <div className="grid gap-2">
        <Label htmlFor="dep-environment">Environment</Label>
        <Select
          items={[
            { value: "none", label: "Not classified" },
            { value: "dev", label: "Dev" },
            { value: "uat", label: "UAT" },
            { value: "production", label: "Production" },
          ]}
          value={environment || "none"}
          onValueChange={(v) => {
            if (typeof v === "string") setEnvironment(v === "none" ? "" : v);
          }}
        >
          <SelectTrigger id="dep-environment" className="w-full">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="none">Not classified</SelectItem>
            <SelectItem value="dev">Dev</SelectItem>
            <SelectItem value="uat">UAT</SelectItem>
            <SelectItem value="production">Production</SelectItem>
          </SelectContent>
        </Select>
        <p className="text-xs text-muted-foreground">
          Classification, protection and the Website are facts about the
          environment, not about who copied the files there — they apply here
          exactly as they do to a deployment this app ships, and they are what
          let this one be a project&apos;s UAT or Production release target.
        </p>
      </div>

      <div className="grid grid-cols-2 gap-2">
        <div className="grid content-start gap-2">
          <Label htmlFor="dep-branch">Source branch</Label>
          {inheritedBranch ? (
            <div className="flex h-9 items-center gap-2 rounded-md border bg-muted/40 px-3 text-sm">
              <span className="font-mono">{inheritedBranch}</span>
              <span className="text-xs text-muted-foreground">
                (inherited from the project&apos;s {environment} release branch)
              </span>
            </div>
          ) : (
            branchSelect("dep-branch", branch, setBranch, "Pick a branch")
          )}
          <p className="text-xs text-muted-foreground">
            What gets merged.
          </p>
        </div>
        <div className="grid content-start gap-2">
          <Label htmlFor="dep-target-branch">Target branch</Label>
          {branchSelect(
            "dep-target-branch",
            targetBranch,
            setTargetBranch,
            "Pick a branch"
          )}
          <p className="text-xs text-muted-foreground">
            Where it lands — the branch the other system watches.
          </p>
        </div>
      </div>

      <div className="grid gap-2">
        <Label>Website</Label>
        <div className="grid grid-cols-[8rem_1fr] gap-2">
          <Select
            items={[
              { value: "domain", label: "Domain" },
              { value: "ip", label: "IP" },
            ]}
            value={websiteKind}
            onValueChange={(v) => {
              if (typeof v === "string") setWebsiteKind(v);
            }}
          >
            <SelectTrigger aria-label="Website kind" className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="domain">Domain</SelectItem>
              <SelectItem value="ip">IP</SelectItem>
            </SelectContent>
          </Select>
          <Input
            aria-label="Website URL"
            className="font-mono"
            placeholder={
              websiteKind === "ip"
                ? "http://203.0.113.10:3000"
                : "https://app.example.com"
            }
            value={websiteUrl}
            onChange={(e) => setWebsiteUrl(e.target.value)}
          />
        </div>
        <p className="text-xs text-muted-foreground">
          Where this environment is reachable — the address a tester or agent
          opens to check it.
        </p>
      </div>

      {isOwner && (
        <Label className="flex cursor-pointer items-center gap-2 text-sm font-normal">
          <input
            type="checkbox"
            className="size-4 accent-destructive"
            checked={isProtected}
            onChange={(e) => setIsProtected(e.target.checked)}
          />
          <span>
            <span className="font-medium">Protected</span> — production
            guardrails: owners only, typed confirmation on every run, no
            one-off ref overrides.
          </span>
        </Label>
      )}

      {error && <p className="text-sm font-medium text-destructive">{error}</p>}
      <DialogFooter>
        <Button type="submit" disabled={saving}>
          {saving && <Loader2 className="size-4 animate-spin" />}
          {isEdit ? "Save changes" : "Create deployment"}
        </Button>
      </DialogFooter>
    </form>
  );

  const sectionButton = (i: number) => (
    <div>
      <Button type="button" size="sm" onClick={() => saveSection(i)} disabled={saving}>
        {saving && <Loader2 className="size-4 animate-spin" />}
        {isEdit ? "Save & continue" : "Continue"}
      </Button>
    </div>
  );

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        setOpen(o);
        if (o) {
          setStep(0);
          setVisited(isEdit ? SECTION_COUNT - 1 : 0);
          setError(null);
        }
      }}
    >
      <DialogTrigger
        render={isEdit ? <Button variant="ghost" size="sm" /> : <Button size="sm" />}
      >
        {isEdit ? (
          <>
            <Pencil className="size-3.5" />
            Edit
          </>
        ) : (
          <>
            <Plus className="size-4" />
            New deployment
          </>
        )}
      </DialogTrigger>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{isEdit ? "Edit deployment" : "New deployment"}</DialogTitle>
          <DialogDescription>
            {isExternal
              ? "Where this project ships when somebody else's pipeline does the shipping."
              : "Where and how this project ships — one section at a time."}
          </DialogDescription>
        </DialogHeader>

        {noConnection ? (
          <p className="text-sm text-muted-foreground">
            No GitHub connection found for this org. Connect GitHub in{" "}
            <a href="/settings/github" className="underline underline-offset-4">
              Settings
            </a>{" "}
            first — the deployment form needs the repo&apos;s live branch list.
          </p>
        ) : isExternal ? (
          externalForm
        ) : (
          <form onSubmit={handleSave} className="grid gap-2">
            {kindPicker}
            {/* ---- 1. Basics ---------------------------------------- */}
            <SectionShell
              title="Basics"
              summary={summaries[0]}
              open={step === 0}
              locked={false}
              complete={visited > 0 && validateSection(0) === null}
              onToggle={() => toggleSection(0)}
            >
              <div className="grid gap-2">
                <Label htmlFor="dep-name">Name</Label>
                <Input
                  id="dep-name"
                  placeholder="staging"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                />
              </div>

              <div className="grid gap-2">
                <Label htmlFor="dep-server">Server</Label>
                <Select
                  items={servers.map((s) => ({
                    value: s.id,
                    label: `${s.name} (${s.host})`,
                  }))}
                  value={serverId || null}
                  onValueChange={(v) => {
                    if (typeof v === "string") setServerId(v);
                  }}
                >
                  <SelectTrigger id="dep-server" className="w-full">
                    <SelectValue placeholder="Pick a machine" />
                  </SelectTrigger>
                  <SelectContent>
                    {servers.map((s) => (
                      <SelectItem key={s.id} value={s.id}>
                        {s.name} ({s.host})
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                {servers.length === 0 && (
                  <p className="text-xs text-muted-foreground">
                    No machines registered yet — add one on the{" "}
                    <Link href="/servers" className="underline underline-offset-4">
                      Machines
                    </Link>{" "}
                    page first.
                  </p>
                )}
                {selectedServer && (
                  <p className="text-xs text-muted-foreground">
                    Deploys authenticate as{" "}
                    <span className="font-mono">{selectedServer.username}</span> —{" "}
                    {selectedServer.auth_method === "password"
                      ? "Password set"
                      : `Key set · ${selectedServer.key_fingerprint ?? "fingerprint unknown"}`}
                  </p>
                )}
              </div>

              <div className="grid gap-2">
                <Label htmlFor="dep-environment">Environment</Label>
                <Select
                  items={[
                    { value: "none", label: "Not classified" },
                    { value: "dev", label: "Dev" },
                    { value: "uat", label: "UAT" },
                    { value: "production", label: "Production" },
                  ]}
                  value={environment || "none"}
                  onValueChange={(v) => {
                    if (typeof v === "string") setEnvironment(v === "none" ? "" : v);
                  }}
                >
                  <SelectTrigger id="dep-environment" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="none">Not classified</SelectItem>
                    <SelectItem value="dev">Dev</SelectItem>
                    <SelectItem value="uat">UAT</SelectItem>
                    <SelectItem value="production">Production</SelectItem>
                  </SelectContent>
                </Select>
                <p className="text-xs text-muted-foreground">
                  {environment === "uat" || environment === "production" ? (
                    <>
                      A <span className="font-medium">UAT</span> or{" "}
                      <span className="font-medium">Production</span> deployment
                      needs its environment set and a Website below — that&apos;s
                      what makes it a complete release environment.
                    </>
                  ) : (
                    "Classify a deployment UAT or Production so it fills the environment columns on release records. Leave unclassified for dev tooling or staging."
                  )}
                </p>
              </div>

              {/* US-7.2: the public reachable address for this environment. */}
              <div className="grid gap-2">
                <Label>Website</Label>
                <div className="grid grid-cols-[8rem_1fr] gap-2">
                  <Select
                    items={[
                      { value: "domain", label: "Domain" },
                      { value: "ip", label: "IP" },
                    ]}
                    value={websiteKind}
                    onValueChange={(v) => {
                      if (typeof v === "string") setWebsiteKind(v);
                    }}
                  >
                    <SelectTrigger aria-label="Website kind" className="w-full">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="domain">Domain</SelectItem>
                      <SelectItem value="ip">IP</SelectItem>
                    </SelectContent>
                  </Select>
                  <Input
                    aria-label="Website URL"
                    className="font-mono"
                    placeholder={
                      websiteKind === "ip"
                        ? "http://203.0.113.10:3000"
                        : "https://app.example.com"
                    }
                    value={websiteUrl}
                    onChange={(e) => setWebsiteUrl(e.target.value)}
                  />
                </div>
                <p className="text-xs text-muted-foreground">
                  Where this environment is reachable — the address a tester or
                  agent opens to check it. Needed for a complete UAT/Production
                  deployment. This is not the internal health check URL below.
                </p>
              </div>

              <div className="grid grid-cols-[1fr_8rem] gap-2">
                <div className="grid content-start gap-2">
                  <Label htmlFor="dep-strategy">Release strategy</Label>
                  <Select
                    items={[
                      { value: "releases", label: "Releases (recommended)" },
                      { value: "in-place", label: "In place" },
                    ]}
                    value={strategy}
                    onValueChange={(v) => {
                      if (typeof v === "string") setStrategy(v);
                    }}
                  >
                    <SelectTrigger id="dep-strategy" className="w-full">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="releases">Releases (recommended)</SelectItem>
                      <SelectItem value="in-place">In place</SelectItem>
                    </SelectContent>
                  </Select>
                  <p className="text-xs text-muted-foreground">
                    {strategy === "releases"
                      ? "Each run lands in releases/<timestamp> and a `current` symlink flips atomically on success — failed deploys never touch the live app, rollback is one click. Point your machine's web-server config at <target>/current."
                      : "Files land straight in the target folder — simple, but a failed deploy can leave it half-updated and there is nothing to roll back to."}
                  </p>
                </div>
                {strategy === "releases" && (
                  <div className="grid content-start gap-2">
                    <Label htmlFor="dep-keep">Keep releases</Label>
                    <Input
                      id="dep-keep"
                      inputMode="numeric"
                      value={keepReleases}
                      onChange={(e) => setKeepReleases(e.target.value)}
                    />
                  </div>
                )}
              </div>
              {sectionButton(0)}
            </SectionShell>

            {/* ---- 2. Source & target -------------------------------- */}
            <SectionShell
              title="Source & target"
              summary={summaries[1]}
              open={step === 1}
              locked={visited < 1}
              complete={visited > 1 && validateSection(1) === null}
              onToggle={() => toggleSection(1)}
            >
              <div className="grid grid-cols-[1fr_8rem] gap-2">
                <div className="grid content-start gap-2">
                  <Label htmlFor="dep-branch">Branch</Label>
                  {inheritedBranch ? (
                    <div className="flex h-9 items-center gap-2 rounded-md border bg-muted/40 px-3 text-sm">
                      <span className="font-mono">{inheritedBranch}</span>
                      <span className="text-xs text-muted-foreground">
                        (inherited from the project&apos;s {environment} release
                        branch)
                      </span>
                    </div>
                  ) : (
                    branchSelect("dep-branch", branch, setBranch, "Pick a branch")
                  )}
                </div>
                <div className="grid content-start gap-2">
                  <Label htmlFor="dep-timeout">Timeout (min)</Label>
                  <Input
                    id="dep-timeout"
                    inputMode="numeric"
                    value={timeout}
                    onChange={(e) => setTimeoutMinutes(e.target.value)}
                  />
                </div>
              </div>

              <div className="grid gap-2">
                <Label htmlFor="dep-folder">Target folder</Label>
                <Input
                  id="dep-folder"
                  placeholder="/var/www/myapp"
                  className="font-mono"
                  value={targetFolder}
                  onChange={(e) => setTargetFolder(e.target.value)}
                />
              </div>

              <div className="grid grid-cols-2 gap-2">
                <div className="grid content-start gap-2">
                  <Label htmlFor="dep-source">Source folder (optional)</Label>
                  <Input
                    id="dep-source"
                    placeholder="apps/web (default: repo root)"
                    className="font-mono"
                    value={sourceFolder}
                    onChange={(e) => setSourceFolder(e.target.value)}
                  />
                  <p className="text-xs text-muted-foreground">
                    Only this folder&apos;s contents ship. Branch deploys only —
                    zips ship as-is.
                  </p>
                </div>
                <div className="grid content-start gap-2">
                  <Label htmlFor="dep-excludes">Exclude patterns (optional)</Label>
                  <Textarea
                    id="dep-excludes"
                    rows={3}
                    spellCheck={false}
                    className="h-20 resize-none overflow-auto font-mono text-xs field-sizing-fixed"
                    placeholder={"*.md\ntests/\n.env.example"}
                    value={excludePatterns}
                    onChange={(e) => setExcludePatterns(e.target.value)}
                  />
                  <p className="text-xs text-muted-foreground">
                    gitignore-style, one per line — removed before transfer.
                  </p>
                </div>
              </div>
              {sectionButton(1)}
            </SectionShell>

            {/* ---- 3. Deployment script ------------------------------ */}
            <SectionShell
              title="Deployment script"
              summary={summaries[2]}
              open={step === 2}
              locked={visited < 2}
              complete={visited > 2}
              onToggle={() => toggleSection(2)}
            >
              <div className="grid gap-2">
                <div className="flex items-center justify-between gap-2">
                  <Label htmlFor="dep-script">Script</Label>
                  <div className="flex items-center gap-2">
                    {!llmConfigured && (
                      <p className="text-xs text-muted-foreground">
                        Configure an LLM in{" "}
                        <Link
                          href="/settings/llm-providers"
                          className="underline underline-offset-2"
                        >
                          Settings
                        </Link>{" "}
                        first.
                      </p>
                    )}
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={handleGenerateScript}
                      disabled={!llmConfigured || generating}
                      title={
                        llmConfigured
                          ? "Draft a script from project overview, guidelines, and this form"
                          : "Configure an LLM provider in Settings first"
                      }
                    >
                      {generating ? (
                        <Loader2 className="size-4 animate-spin" />
                      ) : (
                        <Sparkles className="size-4" />
                      )}
                      Generate with AI
                    </Button>
                  </div>
                </div>
                {draftModel && (
                  <p className="text-xs text-muted-foreground">
                    drafted by {draftModel}
                    {draftGrounded === true ? (
                      <>
                        {" · "}grounded in this project&apos;s{" "}
                        <Link
                          className="underline underline-offset-2 hover:text-foreground"
                          href={`/projects/${projectId}?tab=guidelines`}
                        >
                          Deployment and Release
                        </Link>{" "}
                        section
                      </>
                    ) : draftGrounded === false ? (
                      <>
                        {" · "}
                        <span className="text-amber-600 dark:text-amber-400">
                          not grounded
                        </span>
                        {" — this project has no Deployment and Release "}
                        section, so the draft is from its stack and
                        convention, not its actual deploy. A{" "}
                        <Link
                          className="underline underline-offset-2 hover:text-foreground"
                          href={`/projects/${projectId}?tab=overview`}
                        >
                          guidelines refresh
                        </Link>{" "}
                        writes one from the repo.
                      </>
                    ) : null}
                  </p>
                )}
                <Textarea
                  id="dep-script"
                  rows={8}
                  spellCheck={false}
                  wrap="off"
                  className="h-44 resize-none overflow-auto font-mono text-xs field-sizing-fixed"
                  placeholder={"npm ci\nnpm run build\nsudo systemctl restart myapp"}
                  value={script}
                  onChange={(e) => {
                    setScript(e.target.value);
                    if (draftModel) setDraftModel(null);
                  }}
                />
                <p className="text-xs text-muted-foreground">
                  Runs on the server in the target folder after files are
                  transferred. Leave empty for a transfer-only deployment.
                  Generated drafts are not saved until you click{" "}
                  {isEdit ? "Save changes" : "Create deployment"}.
                </p>
                {generateError && (
                  <p className="text-sm font-medium text-destructive">
                    {generateError}
                  </p>
                )}
              </div>
              {sectionButton(2)}
            </SectionShell>

            {/* ---- 4. Health check & protection (optional) ----------- */}
            <SectionShell
              title="Health check & protection (optional)"
              summary={summaries[3]}
              open={step === 3}
              locked={visited < 3}
              complete={visited >= 3 && validateSection(3) === null}
              onToggle={() => toggleSection(3)}
            >
              <div className="grid gap-2">
                <Label htmlFor="dep-health">Health check URL</Label>
                <div className="grid grid-cols-[1fr_6rem_6rem_6rem] gap-2">
                  <Input
                    id="dep-health"
                    placeholder="http://localhost:3000/health"
                    className="font-mono"
                    value={healthUrl}
                    onChange={(e) => setHealthUrl(e.target.value)}
                  />
                  <Input
                    aria-label="Expected status"
                    inputMode="numeric"
                    title="Expected HTTP status"
                    value={healthStatus}
                    onChange={(e) => setHealthStatus(e.target.value)}
                  />
                  <Input
                    aria-label="Window seconds"
                    inputMode="numeric"
                    title="Retry window (seconds)"
                    value={healthWindow}
                    onChange={(e) => setHealthWindow(e.target.value)}
                  />
                  <Input
                    aria-label="Initial delay seconds"
                    inputMode="numeric"
                    title="Initial delay (seconds)"
                    value={healthDelay}
                    onChange={(e) => setHealthDelay(e.target.value)}
                  />
                </div>
                <p className="text-xs text-muted-foreground">
                  Checked from the target server after the script (so
                  localhost/private URLs work): expected status, retry window
                  (s), initial delay (s). A run only succeeds once it passes;
                  in releases mode a failure auto-rolls-back.
                </p>
              </div>

              {isOwner && (
                <Label className="flex cursor-pointer items-center gap-2 text-sm font-normal">
                  <input
                    type="checkbox"
                    className="size-4 accent-destructive"
                    checked={isProtected}
                    onChange={(e) => setIsProtected(e.target.checked)}
                  />
                  <span>
                    <span className="font-medium">Protected</span> — production
                    guardrails: owners only, typed confirmation on every run, no
                    future automation.
                  </span>
                </Label>
              )}
            </SectionShell>

            {error && <p className="text-sm font-medium text-destructive">{error}</p>}
            <DialogFooter>
              <Button
                type="submit"
                disabled={saving || (!isEdit && visited < SECTION_COUNT - 1)}
                title={
                  !isEdit && visited < SECTION_COUNT - 1
                    ? "Work through the sections above first"
                    : undefined
                }
              >
                {saving && <Loader2 className="size-4 animate-spin" />}
                {isEdit ? "Save changes" : "Create deployment"}
              </Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}
