-- 004_tasks: tasks + append-only task_events (US-1.6), realtime wiring
-- for the live board (US-1.7). Org-scoped + RLS like everything else.

create table public.tasks (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  project_id uuid not null references public.projects(id) on delete cascade,
  title text not null,
  story text,
  acceptance_criteria jsonb not null default '[]'::jsonb,
  status text not null default 'draft'
    check (status in ('draft', 'queued', 'running', 'needs-fixes', 'in-review', 'merged', 'failed')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index tasks_org_idx on public.tasks (org_id);
create index tasks_project_idx on public.tasks (project_id);

create table public.task_events (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  task_id uuid not null references public.tasks(id) on delete cascade,
  type text not null,
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index task_events_task_idx on public.task_events (task_id, created_at);

alter table public.tasks enable row level security;
alter table public.task_events enable row level security;

create policy "members manage their org tasks"
  on public.tasks for all
  using (public.is_org_member(org_id))
  with check (public.is_org_member(org_id));

-- Append-only: members can read and insert events; no update/delete policies.
create policy "members read their org task events"
  on public.task_events for select
  using (public.is_org_member(org_id));

create policy "members append task events"
  on public.task_events for insert
  with check (public.is_org_member(org_id));

create trigger tasks_touch
  before update on public.tasks
  for each row execute function public.touch_updated_at();

-- Realtime: the board (US-1.7) subscribes to task changes. Full replica
-- identity so UPDATE events carry enough for RLS-checked delivery.
alter table public.tasks replica identity full;
alter publication supabase_realtime add table public.tasks;
