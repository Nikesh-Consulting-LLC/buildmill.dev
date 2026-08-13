# Archive/Delete Projects and Tasks (us-1.25) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a PM archive/restore/delete projects and abandon/restore/delete tasks, with a running task guarded against removal.

**Architecture:** Two nullable timestamp columns (`projects.archived_at`, `tasks.abandoned_at`) plus a Postgres trigger that blocks deleting or abandoning a `queued`/`running` task — enforced server-side so it holds regardless of which client makes the call. All mutations are plain Supabase-SDK calls from `apps/web` (no new FastAPI surface, matching the existing "Build less API" pattern used by `project-dialog.tsx`/`revert-button.tsx`). A shared `ConfirmDialog` component (new) backs the two irreversible actions; archive/restore/abandon/restore are single-click since they're reversible.

**Tech Stack:** Next.js 16 App Router, Supabase JS SDK, Postgres trigger, existing Base UI `Dialog`/`Tabs` components.

## Global Constraints

- Migration goes in `infra/supabase/migrations/`, next free number is **`015`** (after `014_profile_avatar.sql` from the profile-management plan — check `infra/supabase/migrations/` before running and bump if `014` is taken by something else). Apply via MCP `apply_migration`, then regenerate `apps/web/src/lib/supabase/database.types.ts` via MCP `generate_typescript_types`.
- `apps/web` has no test runner configured; the user has chosen to skip adding one. Steps replace "test-first" with "implement → `npm run build` → manual browser verification → commit."
- The running-task guard is a **database trigger**, not app code, so it can't be exercised by `apps/api`'s pytest harness (which mocks the DB entirely — see `apps/api/tests/conftest.py`) or by any frontend test runner. It's verified with a direct SQL check during migration application (Task 1, Step 4) and again manually in the browser (Task 4). Say so plainly in the final summary rather than claiming pytest coverage this repo has no harness for.
- Existing `projects`/`tasks` RLS policies (`for all using (public.is_org_member(org_id))`) already cover archived/abandoned rows — no new policies needed, per the story's own acceptance criteria.
- shadcn here is Base UI: triggers use `render={<Button />}`, not `asChild`.
- No comments in code unless explaining a genuinely non-obvious constraint.

---

### Task 1: Migration — archive/abandon columns + running-task delete guard

**Files:**
- Create: `infra/supabase/migrations/015_archive_delete.sql`
- Modify (regenerate): `apps/web/src/lib/supabase/database.types.ts`

**Interfaces:**
- Produces: `public.projects.archived_at timestamptz` (nullable), `public.tasks.abandoned_at timestamptz` (nullable). Trigger `guard_task_removal()` raises on delete or on `abandoned_at` transitioning from null to non-null while `status in ('queued','running')`.

- [ ] **Step 1: Write the migration**

```sql
-- 015_archive_delete: soft-remove (archive/abandon) and hard-delete for
-- projects and tasks (US-1.25). A running/queued task can't be deleted
-- or abandoned outright — the guard trigger blocks it server-side so the
-- rule holds no matter which client makes the call.

alter table public.projects add column archived_at timestamptz;
alter table public.tasks add column abandoned_at timestamptz;

create index projects_active_idx on public.projects (org_id) where archived_at is null;
create index tasks_active_idx on public.tasks (project_id) where abandoned_at is null;

create or replace function public.guard_task_removal()
returns trigger
language plpgsql
as $$
begin
  if TG_OP = 'DELETE' then
    if old.status in ('queued', 'running') then
      raise exception 'Cannot delete a task that is queued or running.';
    end if;
    return old;
  end if;

  if new.abandoned_at is not null
     and old.abandoned_at is null
     and new.status in ('queued', 'running') then
    raise exception 'Cannot abandon a task that is queued or running.';
  end if;
  return new;
end;
$$;

create trigger tasks_guard_delete
  before delete on public.tasks
  for each row execute function public.guard_task_removal();

create trigger tasks_guard_abandon
  before update of abandoned_at on public.tasks
  for each row execute function public.guard_task_removal();
```

- [ ] **Step 2: Apply the migration to the live Supabase project**

Use the Supabase MCP `apply_migration` tool, project id `wdudmfhhqxrqzoyhuzwx`, name `archive_delete`, with the SQL above.

- [ ] **Step 3: Regenerate TypeScript types**

Use the Supabase MCP `generate_typescript_types` tool for project `wdudmfhhqxrqzoyhuzwx` and overwrite `apps/web/src/lib/supabase/database.types.ts`.

- [ ] **Step 4: Verify the guard trigger with direct SQL**

Use the Supabase MCP `execute_sql` tool to create a throwaway `running` task under an existing test project/org and confirm both of these raise:

```sql
-- setup (use a real org_id/project_id from list_tables/select, then):
insert into public.tasks (org_id, project_id, title, status)
values ('<org_id>', '<project_id>', 'guard-check', 'running')
returning id;

-- expect: ERROR — Cannot delete a task that is queued or running.
delete from public.tasks where id = '<returned id>';

-- expect: ERROR — Cannot abandon a task that is queued or running.
update public.tasks set abandoned_at = now() where id = '<returned id>';

-- cleanup: flip status first, then delete
update public.tasks set status = 'failed' where id = '<returned id>';
delete from public.tasks where id = '<returned id>';
```

- [ ] **Step 5: Commit**

```bash
git add infra/supabase/migrations/015_archive_delete.sql apps/web/src/lib/supabase/database.types.ts
git commit -m "feat: add archive/abandon columns and running-task removal guard"
```

---

### Task 2: Shared `ConfirmDialog` component

**Files:**
- Create: `apps/web/src/components/confirm-dialog.tsx`

**Interfaces:**
- Produces: `ConfirmDialog({ trigger, title, description, confirmLabel, onConfirm }: { trigger: React.ReactNode; title: string; description: string; confirmLabel: string; onConfirm: () => Promise<void> })` — used by Tasks 3 and 5.

- [ ] **Step 1: Implement**

```tsx
// apps/web/src/components/confirm-dialog.tsx
"use client";

import { useState } from "react";
import { Loader2 } from "lucide-react";
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

export function ConfirmDialog({
  trigger,
  title,
  description,
  confirmLabel,
  onConfirm,
}: {
  trigger: React.ReactNode;
  title: string;
  description: string;
  confirmLabel: string;
  onConfirm: () => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleConfirm() {
    setError(null);
    setBusy(true);
    try {
      await onConfirm();
      setOpen(false);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger render={trigger as React.ReactElement} />
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        {error && <p className="text-sm font-medium text-destructive">{error}</p>}
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)} disabled={busy}>
            Cancel
          </Button>
          <Button variant="destructive" onClick={handleConfirm} disabled={busy}>
            {busy && <Loader2 className="size-4 animate-spin" />}
            {confirmLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
```

- [ ] **Step 2: Build check**

Run: `npm run build`.

- [ ] **Step 3: Commit**

```bash
git add apps/web/src/components/confirm-dialog.tsx
git commit -m "feat: add reusable destructive-action confirm dialog"
```

---

### Task 3: Project archive/restore/delete — actions + list/detail wiring

**Files:**
- Create: `apps/web/src/app/(app)/projects/project-actions.tsx`
- Modify: `apps/web/src/app/(app)/projects/page.tsx`
- Modify: `apps/web/src/app/(app)/projects/[id]/page.tsx`

**Interfaces:**
- Consumes: `ConfirmDialog` (Task 2), `projects.archived_at` (Task 1).
- Produces: `ProjectActions({ projectId, name, archivedAt, redirectOnDelete }: { projectId: string; name: string; archivedAt: string | null; redirectOnDelete?: boolean })`.

- [ ] **Step 1: `ProjectActions` component**

```tsx
// apps/web/src/app/(app)/projects/project-actions.tsx
"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Archive, ArchiveRestore, Loader2, Trash2 } from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/confirm-dialog";

export function ProjectActions({
  projectId,
  name,
  archivedAt,
  redirectOnDelete = false,
}: {
  projectId: string;
  name: string;
  archivedAt: string | null;
  redirectOnDelete?: boolean;
}) {
  const router = useRouter();
  const [toggling, setToggling] = useState(false);

  async function toggleArchive() {
    setToggling(true);
    const supabase = createClient();
    try {
      const { error } = await supabase
        .from("projects")
        .update({ archived_at: archivedAt ? null : new Date().toISOString() })
        .eq("id", projectId);
      if (!error) router.refresh();
    } finally {
      setToggling(false);
    }
  }

  async function handleDelete() {
    const supabase = createClient();
    const { error } = await supabase.from("projects").delete().eq("id", projectId);
    if (error) throw new Error(error.message);
    if (redirectOnDelete) {
      router.push("/projects");
    } else {
      router.refresh();
    }
  }

  return (
    <div className="flex shrink-0 items-center gap-2">
      <Button variant="outline" size="sm" onClick={toggleArchive} disabled={toggling}>
        {toggling ? (
          <Loader2 className="size-4 animate-spin" />
        ) : archivedAt ? (
          <ArchiveRestore className="size-4" />
        ) : (
          <Archive className="size-4" />
        )}
        {archivedAt ? "Restore" : "Archive"}
      </Button>
      <ConfirmDialog
        trigger={
          <Button variant="outline" size="sm">
            <Trash2 className="size-4" />
            Delete
          </Button>
        }
        title={`Delete "${name}"?`}
        description="This permanently deletes the project and all of its tasks, runs, and reviews. This can't be undone."
        confirmLabel="Delete project"
        onConfirm={handleDelete}
      />
    </div>
  );
}
```

- [ ] **Step 2: Wire into the projects list — add Active/Archived tabs**

Replace `apps/web/src/app/(app)/projects/page.tsx`'s query section with a tab-aware version:

```tsx
// apps/web/src/app/(app)/projects/page.tsx
import Link from "next/link";
import { redirect } from "next/navigation";
import { FolderGit2, GitBranch } from "lucide-react";
import { createClient } from "@/lib/supabase/server";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { EmptyState } from "@/components/empty-state";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ProjectDialog } from "./project-dialog";
import { ProjectActions } from "./project-actions";

export default async function ProjectsPage({
  searchParams,
}: {
  searchParams: Promise<{ view?: string }>;
}) {
  const { view } = await searchParams;
  const showArchived = view === "archived";
  const supabase = await createClient();

  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/login");

  const { data: membership } = await supabase
    .from("organization_members")
    .select("org_id")
    .eq("user_id", user.id)
    .limit(1)
    .maybeSingle();
  if (!membership) redirect("/login");

  const { data: projects } = await supabase
    .from("projects")
    .select("id, name, description, repo_full_name, default_branch, updated_at, archived_at")
    .order("created_at", { ascending: false });

  const active = (projects ?? []).filter((p) => !p.archived_at);
  const archived = (projects ?? []).filter((p) => p.archived_at);
  const list = showArchived ? archived : active;

  return (
    <div className="mx-auto flex w-full max-w-5xl flex-col gap-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Projects</h1>
          <p className="text-sm text-muted-foreground">
            Each project links the factory to a GitHub repository.
          </p>
        </div>
        <ProjectDialog orgId={membership.org_id} />
      </div>

      <Tabs value={showArchived ? "archived" : "active"}>
        <TabsList>
          <TabsTrigger value="active" render={<Link href="/projects" />}>
            Active ({active.length})
          </TabsTrigger>
          <TabsTrigger value="archived" render={<Link href="/projects?view=archived" />}>
            Archived ({archived.length})
          </TabsTrigger>
        </TabsList>

        <TabsContent value={showArchived ? "archived" : "active"}>
          {!list.length ? (
            <EmptyState
              icon={FolderGit2}
              title={showArchived ? "No archived projects" : "No projects yet"}
              description={
                showArchived
                  ? "Projects you archive will show up here."
                  : "Create your first project to point the factory at a repository."
              }
            />
          ) : (
            <div className="grid gap-4 sm:grid-cols-2">
              {list.map((p) => (
                <Card key={p.id} className="h-full transition-colors hover:border-ring/60">
                  <CardHeader className="flex flex-row items-start justify-between space-y-0">
                    <Link href={`/projects/${p.id}`} className="min-w-0 flex-1">
                      <CardTitle className="flex items-center gap-2 text-base">
                        <FolderGit2 className="size-4 text-muted-foreground" />
                        <span className="truncate">{p.name}</span>
                      </CardTitle>
                      {p.description && (
                        <CardDescription className="line-clamp-2">
                          {p.description}
                        </CardDescription>
                      )}
                    </Link>
                  </CardHeader>
                  <CardContent className="flex items-center justify-between gap-2 text-sm text-muted-foreground">
                    <span className="flex items-center gap-2">
                      <span className="truncate font-mono text-xs">{p.repo_full_name}</span>
                      <Badge variant="secondary" className="gap-1 font-normal">
                        <GitBranch className="size-3" />
                        {p.default_branch}
                      </Badge>
                    </span>
                    <ProjectActions
                      projectId={p.id}
                      name={p.name}
                      archivedAt={p.archived_at}
                    />
                  </CardContent>
                </Card>
              ))}
            </div>
          )}
        </TabsContent>
      </Tabs>
    </div>
  );
}
```

- [ ] **Step 3: Wire into the project detail page**

In `apps/web/src/app/(app)/projects/[id]/page.tsx`:
1. Add `archived_at` to the `projects` select (`"id, org_id, name, description, repo_full_name, default_branch, created_at, updated_at, issue_sync_enabled, issue_sync_last_pulled_at, archived_at"`).
2. Import `ProjectActions` from `../project-actions`.
3. In the header row (next to `<ProjectDialog orgId={project.org_id} project={project} />`), add:

```tsx
<div className="flex shrink-0 items-center gap-2">
  <ProjectDialog orgId={project.org_id} project={project} />
  <ProjectActions
    projectId={project.id}
    name={project.name}
    archivedAt={project.archived_at}
    redirectOnDelete
  />
</div>
```

(replacing the current bare `<ProjectDialog .../>` on its own).

4. If `project.archived_at` is set, show an `Archived` badge next to the title (reuse the existing `Badge` import): `{project.archived_at && <Badge variant="secondary">Archived</Badge>}` placed after the `<h1>`.

- [ ] **Step 4: Build check**

Run: `npm run build`.

- [ ] **Step 5: Manual verification**

In the browser preview: archive a project from the list, confirm it disappears from Active and appears under Archived; restore it and confirm it reappears in Active; delete a throwaway project via the detail page and confirm the confirm dialog names it and it's gone after confirming.

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/app/\(app\)/projects/project-actions.tsx apps/web/src/app/\(app\)/projects/page.tsx apps/web/src/app/\(app\)/projects/\[id\]/page.tsx
git commit -m "feat: archive/restore/delete projects with Active/Archived tabs"
```

---

### Task 4: Task abandon/restore/delete — actions on task detail

**Files:**
- Create: `apps/web/src/app/(app)/tasks/task-actions.tsx`
- Modify: `apps/web/src/app/(app)/tasks/[id]/page.tsx`

**Interfaces:**
- Consumes: `ConfirmDialog` (Task 2), `tasks.abandoned_at`, `tasks.status` (Task 1 / existing).
- Produces: `TaskActions({ taskId, title, status, abandonedAt }: { taskId: string; title: string; status: string; abandonedAt: string | null })`.

- [ ] **Step 1: `TaskActions` component**

```tsx
// apps/web/src/app/(app)/tasks/task-actions.tsx
"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Archive, ArchiveRestore, Loader2, Trash2 } from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/confirm-dialog";

const RUNNING_STATUSES = ["queued", "running"];

export function TaskActions({
  taskId,
  title,
  status,
  abandonedAt,
}: {
  taskId: string;
  title: string;
  status: string;
  abandonedAt: string | null;
}) {
  const router = useRouter();
  const [toggling, setToggling] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const blocked = RUNNING_STATUSES.includes(status);

  async function toggleAbandon() {
    setError(null);
    setToggling(true);
    const supabase = createClient();
    try {
      const { error: dbError } = await supabase
        .from("tasks")
        .update({ abandoned_at: abandonedAt ? null : new Date().toISOString() })
        .eq("id", taskId);
      if (dbError) {
        setError(dbError.message);
        return;
      }
      router.refresh();
    } finally {
      setToggling(false);
    }
  }

  async function handleDelete() {
    const supabase = createClient();
    const { error: dbError } = await supabase.from("tasks").delete().eq("id", taskId);
    if (dbError) throw new Error(dbError.message);
    router.push(`/tasks`);
  }

  return (
    <div className="flex shrink-0 items-center gap-2">
      {error && <p className="text-xs font-medium text-destructive">{error}</p>}
      <Button
        variant="outline"
        size="sm"
        onClick={toggleAbandon}
        disabled={toggling || (blocked && !abandonedAt)}
        title={
          blocked && !abandonedAt
            ? "Cancel the run before abandoning this task."
            : undefined
        }
      >
        {toggling ? (
          <Loader2 className="size-4 animate-spin" />
        ) : abandonedAt ? (
          <ArchiveRestore className="size-4" />
        ) : (
          <Archive className="size-4" />
        )}
        {abandonedAt ? "Restore" : "Abandon"}
      </Button>
      <ConfirmDialog
        trigger={
          <Button variant="outline" size="sm" disabled={blocked}>
            <Trash2 className="size-4" />
            Delete
          </Button>
        }
        title={`Delete "${title}"?`}
        description="This permanently deletes the task and its events, runs, and reviews. This can't be undone."
        confirmLabel="Delete task"
        onConfirm={handleDelete}
      />
    </div>
  );
}
```

- [ ] **Step 2: Wire into task detail page**

In `apps/web/src/app/(app)/tasks/[id]/page.tsx`:
1. Add `abandoned_at` to the `tasks` select.
2. Import `TaskActions` from `../task-actions`.
3. Add `<TaskActions taskId={task.id} title={task.title} status={task.status} abandonedAt={task.abandoned_at} />` into the header's action row, alongside the existing `TaskDialog`/`RevertButton`/`DispatchButton`.
4. If `task.abandoned_at` is set, render an "Abandoned" `Badge` next to the existing `StatusBadge`.

- [ ] **Step 3: Build check**

Run: `npm run build`.

- [ ] **Step 4: Manual verification**

Abandon a `draft` task from its detail page, confirm it drops off the task board (Task 5 makes the board query filter it) and reappears in its original status column after restoring. Then set a task to `running` (or use one already running) and confirm the Abandon/Delete buttons are disabled with the tooltip message.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/app/\(app\)/tasks/task-actions.tsx apps/web/src/app/\(app\)/tasks/\[id\]/page.tsx
git commit -m "feat: abandon/restore/delete tasks from the task detail page"
```

---

### Task 5: Task board — filter out abandoned tasks + Abandoned tab

**Files:**
- Modify: `apps/web/src/app/(app)/tasks/page.tsx`
- Create: `apps/web/src/app/(app)/tasks/abandoned-task-list.tsx`

**Interfaces:**
- Consumes: `TaskActions` (Task 4), existing `TaskBoard`/`BoardTask` from `task-board.tsx`.
- Produces: `AbandonedTaskList({ tasks }: { tasks: Array<{ id: string; title: string; status: string; updated_at: string }> })`.

- [ ] **Step 1: Abandoned task list component**

```tsx
// apps/web/src/app/(app)/tasks/abandoned-task-list.tsx
import Link from "next/link";
import { StatusBadge, type TaskStatus } from "@/components/status-badge";
import { EmptyState } from "@/components/empty-state";
import { Archive } from "lucide-react";

export function AbandonedTaskList({
  tasks,
}: {
  tasks: Array<{ id: string; title: string; status: string; updated_at: string }>;
}) {
  if (!tasks.length) {
    return (
      <EmptyState
        icon={Archive}
        title="No abandoned tasks"
        description="Tasks you abandon will show up here until restored or deleted."
      />
    );
  }

  return (
    <ul className="grid gap-1.5">
      {tasks.map((t) => (
        <li key={t.id}>
          <Link
            href={`/tasks/${t.id}`}
            className="flex items-center justify-between gap-3 rounded-md border px-3 py-2 text-sm transition-colors hover:border-ring/60"
          >
            <span className="truncate font-medium">{t.title}</span>
            <StatusBadge status={t.status as TaskStatus} />
          </Link>
        </li>
      ))}
    </ul>
  );
}
```

(Restore/delete for an abandoned task happens on its detail page via `TaskActions` from Task 4 — clicking through keeps this list simple and reuses the existing action surface instead of duplicating buttons.)

- [ ] **Step 2: Wire tabs + filtered queries into `tasks/page.tsx`**

Replace the tasks-query section of `apps/web/src/app/(app)/tasks/page.tsx`:

```tsx
// apps/web/src/app/(app)/tasks/page.tsx
import Link from "next/link";
import { redirect } from "next/navigation";
import { FolderGit2 } from "lucide-react";
import { createClient } from "@/lib/supabase/server";
import { cn } from "@/lib/utils";
import { EmptyState } from "@/components/empty-state";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { TaskBoard, type BoardTask } from "./task-board";
import { AbandonedTaskList } from "./abandoned-task-list";

export default async function TasksPage({
  searchParams,
}: {
  searchParams: Promise<{ project?: string; view?: string }>;
}) {
  const { project: projectParam, view } = await searchParams;
  const showAbandoned = view === "abandoned";
  const supabase = await createClient();

  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/login");

  const { data: projects } = await supabase
    .from("projects")
    .select("id, name")
    .order("created_at", { ascending: true });

  if (!projects?.length) {
    return (
      <div className="mx-auto flex w-full max-w-5xl flex-col gap-6">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Tasks</h1>
          <p className="text-sm text-muted-foreground">
            The board — every task by status, updating live.
          </p>
        </div>
        <EmptyState
          icon={FolderGit2}
          title="No projects yet"
          description="Create a project first — tasks live inside projects."
        />
      </div>
    );
  }

  const selected = projects.find((p) => p.id === projectParam) ?? projects[0];

  const { data: activeTasks } = await supabase
    .from("tasks")
    .select("id, title, status, updated_at, github_issue_number, github_issue_url")
    .eq("project_id", selected.id)
    .is("abandoned_at", null)
    .order("updated_at", { ascending: false });

  const { data: abandonedTasks } = await supabase
    .from("tasks")
    .select("id, title, status, updated_at")
    .eq("project_id", selected.id)
    .not("abandoned_at", "is", null)
    .order("updated_at", { ascending: false });

  return (
    <div className="flex w-full flex-col gap-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Tasks</h1>
          <p className="text-sm text-muted-foreground">
            The board — every task by status, updating live.
          </p>
        </div>
        <div className="flex flex-wrap gap-1.5">
          {projects.map((p) => (
            <Link
              key={p.id}
              href={`/tasks?project=${p.id}${showAbandoned ? "&view=abandoned" : ""}`}
              className={cn(
                "rounded-full border px-3 py-1 text-xs font-medium transition-colors",
                p.id === selected.id
                  ? "border-transparent bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:text-foreground"
              )}
            >
              {p.name}
            </Link>
          ))}
        </div>
      </div>

      <Tabs value={showAbandoned ? "abandoned" : "board"}>
        <TabsList>
          <TabsTrigger value="board" render={<Link href={`/tasks?project=${selected.id}`} />}>
            Board
          </TabsTrigger>
          <TabsTrigger
            value="abandoned"
            render={<Link href={`/tasks?project=${selected.id}&view=abandoned`} />}
          >
            Abandoned ({abandonedTasks?.length ?? 0})
          </TabsTrigger>
        </TabsList>

        <TabsContent value="board">
          <TaskBoard projectId={selected.id} initialTasks={(activeTasks as BoardTask[]) ?? []} />
        </TabsContent>

        <TabsContent value="abandoned">
          <AbandonedTaskList tasks={abandonedTasks ?? []} />
        </TabsContent>
      </Tabs>
    </div>
  );
}
```

Note: `TaskBoard`'s realtime subscription (`task-board.tsx`) listens to all `UPDATE`/`INSERT`/`DELETE` events for the project unfiltered by `abandoned_at` — when a task is abandoned via the detail page the board won't auto-remove it client-side until the next `router.refresh()`/navigation. That's acceptable for this story (abandon/restore happen from the detail page, not the board), but note it in the final summary as a known limitation rather than silently glossing over it.

- [ ] **Step 3: Build check**

Run: `npm run build`.

- [ ] **Step 4: Manual verification**

Confirm the Abandoned tab shows the task abandoned in Task 4's verification step, and that the Board tab no longer shows it. Restore it from its detail page and confirm it reappears on the board in its original status column.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/app/\(app\)/tasks/page.tsx apps/web/src/app/\(app\)/tasks/abandoned-task-list.tsx
git commit -m "feat: add Abandoned tab and filter abandoned tasks off the board"
```
