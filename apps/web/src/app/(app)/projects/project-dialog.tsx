"use client";

import { useEffect, useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import { Loader2, Pencil, Plus, RotateCw } from "lucide-react";
import { useGithubRepos } from "@/lib/use-github-repos";
import { createClient } from "@/lib/supabase/client";
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

export type ProjectFormData = {
  id: string;
  name: string;
  description: string | null;
  repo_full_name: string;
  default_branch: string;
};

type OrgTemplate = { id: string; name: string; is_default: boolean };

export function ProjectDialog({
  orgId,
  project,
}: {
  orgId: string;
  project?: ProjectFormData;
}) {
  const router = useRouter();
  const isEdit = !!project;
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(project?.name ?? "");
  const [description, setDescription] = useState(project?.description ?? "");
  const {
    repos,
    loading: reposLoading,
    error: reposError,
    reload: reloadRepos,
  } = useGithubRepos(open);
  const [repoFullName, setRepoFullName] = useState(project?.repo_full_name ?? "");
  const [branch, setBranch] = useState(project?.default_branch ?? "main");
  const [templates, setTemplates] = useState<OrgTemplate[] | null>(null);
  const [templateId, setTemplateId] = useState<string>("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open || isEdit) return;
    const supabase = createClient();
    supabase
      .from("org_project_templates")
      .select("id, name, is_default")
      .eq("org_id", orgId)
      .eq("is_available", true)
      .is("archived_at", null)
      .order("is_default", { ascending: false })
      .then(({ data }) => {
        const list = (data as OrgTemplate[] | null) ?? [];
        setTemplates(list);
        setTemplateId((prev) => prev || list.find((t) => t.is_default)?.id || list[0]?.id || "");
      });
  }, [open, isEdit, orgId]);

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    setError(null);

    if (!name.trim()) {
      setError("Project name is required.");
      return;
    }
    if (!repoFullName) {
      setError("Select a repository.");
      return;
    }

    setSaving(true);
    const supabase = createClient();
    try {
      const values = {
        name: name.trim(),
        description: description.trim() || null,
        repo_full_name: repoFullName,
        default_branch: branch.trim() || "main",
      };

      const { error: dbError } = isEdit
        ? await supabase.from("projects").update(values).eq("id", project.id)
        : await supabase.from("projects").insert({
            ...values,
            org_id: orgId,
            ...(templateId ? { org_template_id: templateId } : {}),
          });

      if (dbError) {
        setError(dbError.message);
        return;
      }

      setOpen(false);
      if (!isEdit) {
        setName("");
        setDescription("");
        setRepoFullName("");
        setBranch("main");
      }
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
            <Button variant="outline" size="sm" />
          ) : (
            <Button variant="create" />
          )
        }
      >
        {isEdit ? (
          <>
            <Pencil className="size-4" />
            Edit
          </>
        ) : (
          <>
            <Plus className="size-4" />
            New project
          </>
        )}
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{isEdit ? "Edit project" : "New project"}</DialogTitle>
          <DialogDescription>
            {isEdit
              ? "Update the project or its linked repository."
              : "Point the factory at a GitHub repository."}
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSave} className="grid gap-4">
          <div className="grid gap-2">
            <Label htmlFor="project-name">Name</Label>
            <Input
              id="project-name"
              placeholder="My product"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </div>
          {isEdit && (
            <div className="grid gap-2">
              <Label htmlFor="project-description">Description</Label>
              <Input
                id="project-description"
                placeholder="What this project is (optional)"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
              />
            </div>
          )}
          {!isEdit && templates && templates.length > 0 && (
            <div className="grid gap-2">
              <Label htmlFor="project-template">Project template</Label>
              <Select
                items={templates.map((t) => ({
                  value: t.id,
                  label: t.is_default ? `${t.name} (Default)` : t.name,
                }))}
                value={templateId}
                onValueChange={(v) => {
                  if (typeof v === "string") setTemplateId(v);
                }}
              >
                <SelectTrigger id="project-template" className="w-full">
                  <SelectValue placeholder="Select a template" />
                </SelectTrigger>
                <SelectContent>
                  {templates.map((t) => (
                    <SelectItem key={t.id} value={t.id}>
                      {t.is_default ? `${t.name} (Default)` : t.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="text-xs text-muted-foreground">
                Seeds this project&apos;s guidelines and worker instructions from
                the template — editable afterward.
              </p>
            </div>
          )}
          <div className="grid gap-2">
            <div className="flex items-center justify-between">
              <Label htmlFor="project-repo">GitHub repository</Label>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="h-6 gap-1 px-2 text-xs text-muted-foreground"
                onClick={reloadRepos}
                disabled={reposLoading}
                title="Reload repositories from GitHub"
              >
                <RotateCw className={`size-3 ${reposLoading ? "animate-spin" : ""}`} />
                Reload
              </Button>
            </div>
            {reposError && !repos ? (
              <p className="text-sm text-muted-foreground">
                Couldn&apos;t load repositories ({reposError}).{" "}
                <a href="/settings/github" className="underline underline-offset-4">
                  Check your GitHub connection
                </a>
                .
              </p>
            ) : repos && repos.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                No repositories available.{" "}
                <a href="/settings/github" className="underline underline-offset-4">
                  Connect GitHub
                </a>{" "}
                and grant access to a repo first.
              </p>
            ) : (
              <>
                <Select
                  items={(repos ?? []).map((r) => ({ value: r.full_name, label: r.full_name }))}
                  value={repoFullName}
                  onValueChange={(v) => {
                    if (typeof v !== "string") return;
                    setRepoFullName(v);
                    const match = repos?.find((r) => r.full_name === v);
                    if (match) setBranch(match.default_branch);
                  }}
                >
                  <SelectTrigger id="project-repo" className="w-full">
                    <SelectValue placeholder={repos ? "Select a repository" : "Loading…"} />
                  </SelectTrigger>
                  <SelectContent>
                    {(repos ?? []).map((r) => (
                      <SelectItem key={r.full_name} value={r.full_name}>
                        {r.full_name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                {reposError && repos && (
                  <p className="text-xs text-muted-foreground">
                    Showing cached repositories — reload failed ({reposError}).
                  </p>
                )}
              </>
            )}
          </div>
          {isEdit && (
            <div className="grid gap-2">
              <Label htmlFor="project-branch">Default branch</Label>
              <Input
                id="project-branch"
                placeholder="main"
                value={branch}
                onChange={(e) => setBranch(e.target.value)}
              />
            </div>
          )}
          {error && (
            <p className="text-sm font-medium text-destructive">{error}</p>
          )}
          <DialogFooter>
            <Button type="submit" disabled={saving}>
              {saving && <Loader2 className="size-4 animate-spin" />}
              {isEdit ? "Save changes" : "Create project"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
