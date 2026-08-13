"use client";

import { useEffect, useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import { GitBranch, Loader2, Plus } from "lucide-react";
import { apiCall, ApiError } from "@/lib/api";
import { createClient } from "@/lib/supabase/client";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

type Branch = { name: string; commit_sha: string };

const STRATEGIES: { value: string; label: string; help: string }[] = [
  {
    value: "story",
    label: "New branch per story",
    help: "Each story gets its own branch and PR.",
  },
  {
    value: "work_item",
    label: "New branch per work item",
    help: "A feature's stories share one branch and PR on the parent; a standalone bug/chore gets its own.",
  },
  {
    value: "main",
    label: "Work on the main branch",
    help: "Agents commit directly to the default branch — no PR, so the PR review gate is bypassed.",
  },
];

/** US-7.3: pick the UAT and Production release branches (the single source of
 * truth for what ships where), create one from the default branch without
 * leaving Build Mill, and choose how agents branch when they write code. */
export function ReleaseBranchesCard({
  projectId,
  repoFullName,
  uatBranch,
  productionBranch,
  devBranchStrategy,
}: {
  projectId: string;
  repoFullName: string;
  uatBranch: string | null;
  productionBranch: string | null;
  devBranchStrategy: string;
}) {
  const router = useRouter();
  const [branches, setBranches] = useState<Branch[] | null>(null);
  const [noConnection, setNoConnection] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [uat, setUat] = useState(uatBranch);
  const [prod, setProd] = useState(productionBranch);
  const [strategy, setStrategy] = useState(devBranchStrategy);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState<"uat" | "production" | null>(null);
  const [newName, setNewName] = useState("");
  const [creatingFor, setCreatingFor] = useState<"uat" | "production" | null>(null);

  useEffect(() => {
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
        else setLoadError((e as Error).message);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [repoFullName]);

  async function saveColumn(
    column: "uat_branch" | "production_branch" | "dev_branch_strategy",
    value: string | null
  ) {
    setError(null);
    const supabase = createClient();
    const { error: dbError } = await supabase
      .from("projects")
      .update({ [column]: value })
      .eq("id", projectId);
    if (dbError) {
      setError(dbError.message);
      return;
    }
    router.refresh();
  }

  async function createBranch() {
    if (!newName.trim() || !creatingFor) return;
    setCreating(creatingFor);
    setError(null);
    try {
      const result = (await apiCall(
        `/api/v1/github/repos/${repoFullName}/branches`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name: newName.trim() }),
        }
      )) as Branch;
      setBranches((b) => [...(b ?? []), result]);
      if (creatingFor === "uat") {
        setUat(result.name);
        await saveColumn("uat_branch", result.name);
      } else {
        setProd(result.name);
        await saveColumn("production_branch", result.name);
      }
      setNewName("");
      setCreatingFor(null);
    } catch (e) {
      setError(
        e instanceof ApiError ? String(e.message) : (e as Error).message
      );
    } finally {
      setCreating(null);
    }
  }

  function BranchPicker({
    kind,
    label,
    value,
    onChange,
  }: {
    kind: "uat" | "production";
    label: string;
    value: string | null;
    onChange: (v: string) => void;
  }) {
    return (
      <div className="grid gap-2">
        <Label>{label}</Label>
        <div className="flex items-center gap-2">
          <Select
            items={(branches ?? []).map((b) => ({ value: b.name, label: b.name }))}
            value={value || null}
            onValueChange={(v) => {
              if (typeof v === "string") onChange(v);
            }}
          >
            <SelectTrigger className="w-full">
              <SelectValue placeholder="Pick a branch" />
            </SelectTrigger>
            <SelectContent>
              {(branches ?? []).map((b) => (
                <SelectItem key={b.name} value={b.name}>
                  {b.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() =>
              setCreatingFor((c) => (c === kind ? null : kind))
            }
            title="Create a branch from the default branch"
          >
            <Plus className="size-4" />
            New branch…
          </Button>
        </div>
        {creatingFor === kind && (
          <div className="flex items-center gap-2">
            <Input
              autoFocus
              placeholder="release/uat"
              className="font-mono"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  createBranch();
                }
              }}
            />
            <Button
              type="button"
              size="sm"
              onClick={createBranch}
              disabled={creating === kind || !newName.trim()}
            >
              {creating === kind && <Loader2 className="size-4 animate-spin" />}
              Create
            </Button>
          </div>
        )}
      </div>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <GitBranch className="size-4 text-muted-foreground" />
          Release branches & branching strategy
        </CardTitle>
        <CardDescription>
          Which branch ships to UAT and which to Production — the single source
          of truth the deployments release from — and how agents branch when
          they write code.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        {noConnection ? (
          <p className="text-sm text-muted-foreground">
            No GitHub connection found. Connect GitHub in{" "}
            <a href="/settings/github" className="underline underline-offset-4">
              Settings
            </a>{" "}
            first — the pickers need the repo&apos;s live branch list.
          </p>
        ) : loadError ? (
          <p className="text-sm font-medium text-destructive">
            Couldn&apos;t load branches: {loadError}
          </p>
        ) : branches === null ? (
          <p className="inline-flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="size-3.5 animate-spin" />
            Loading branches…
          </p>
        ) : (
          <div className="grid gap-4 sm:grid-cols-2">
            <BranchPicker
              kind="uat"
              label="UAT branch"
              value={uat}
              onChange={(v) => {
                setUat(v);
                saveColumn("uat_branch", v);
              }}
            />
            <BranchPicker
              kind="production"
              label="Production branch"
              value={prod}
              onChange={(v) => {
                setProd(v);
                saveColumn("production_branch", v);
              }}
            />
          </div>
        )}

        <div className="grid gap-2">
          <Label>Development branching strategy</Label>
          <Select
            items={STRATEGIES.map((s) => ({ value: s.value, label: s.label }))}
            value={strategy}
            onValueChange={(v) => {
              if (typeof v === "string") {
                setStrategy(v);
                saveColumn("dev_branch_strategy", v);
              }
            }}
          >
            <SelectTrigger className="w-full sm:w-96">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {STRATEGIES.map((s) => (
                <SelectItem key={s.value} value={s.value}>
                  {s.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <p className="text-xs text-muted-foreground">
            {STRATEGIES.find((s) => s.value === strategy)?.help}
          </p>
        </div>

        {error && <p className="text-sm font-medium text-destructive">{error}</p>}
      </CardContent>
    </Card>
  );
}
