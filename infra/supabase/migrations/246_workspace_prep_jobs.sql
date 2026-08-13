-- US-85.1: Prepare Agent Workspace on demand.
--
-- One row per preparation job: the manager asks a connected runner to make
-- its per-project workspace fully ready (directory, latest code, agent + MCP
-- config, tool servers, verification) before any run is dispatched. The
-- popup watches this row over Realtime — `steps` is the checklist, updated
-- as the runner streams `prep.step` notifications over the control socket.
--
-- Same shape agent_server_jobs (142) uses for machine jobs, but keyed to the
-- (worker, project) pair rather than the host: an agent on a shared platform
-- pool has no agent_servers row its own org can read, while this row is
-- org-scoped to the tenant and therefore visible to the manager who clicked.

create table public.workspace_prep_jobs (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  worker_id uuid not null references public.workers(id) on delete cascade,
  project_id uuid not null references public.projects(id) on delete cascade,

  status text not null default 'queued'
    check (status in ('queued', 'running', 'succeeded', 'failed')),
  -- [{key, label, status: pending|running|ok|failed, detail}] — the popup's
  -- checklist, in order. Written only by the API (service role).
  steps jsonb not null default '[]'::jsonb,
  error text,

  -- What a successful preparation left on disk (AC5's caption).
  prepared_commit text,
  workdir text,

  started_by uuid,
  started_by_email text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  started_at timestamptz,
  finished_at timestamptz
);

create index workspace_prep_jobs_org_idx on public.workspace_prep_jobs (org_id);
create index workspace_prep_jobs_pair_idx
  on public.workspace_prep_jobs (worker_id, project_id, created_at desc);

-- One live preparation per (agent, project): a second click reattaches to the
-- running job instead of racing it (AC6).
create unique index workspace_prep_jobs_one_active_key
  on public.workspace_prep_jobs (worker_id, project_id)
  where status in ('queued', 'running');

alter table public.workspace_prep_jobs enable row level security;

-- Read-only for org members; every write goes through the API's service role.
create policy "org members read workspace prep jobs"
  on public.workspace_prep_jobs for select
  using (public.is_org_member(org_id));

create trigger workspace_prep_jobs_touch
  before update on public.workspace_prep_jobs
  for each row execute function public.touch_updated_at();

-- The popup's live checklist rides Realtime, like agent_server_jobs' log.
alter publication supabase_realtime add table public.workspace_prep_jobs;
