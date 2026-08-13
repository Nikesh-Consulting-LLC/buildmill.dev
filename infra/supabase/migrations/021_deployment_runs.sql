-- 021_deployment_runs: run a deployment on demand (US-1.32).
--
-- deployment_runs is the run record; deployment_run_events is the
-- append-only feed of phase transitions, progress ticks, and script
-- output lines that powers the live view (Realtime on inserts) and
-- preserves per-phase timings in history.
--
-- Writes go through `api` only (service role): the run pipeline is
-- orchestrated server-side, so clients get SELECT policies and nothing
-- else — run history is immutable from the browser by construction.
-- Cross-org integrity uses the same composite-FK pattern as 020.

alter table public.deployments
  add constraint deployments_id_org_unique unique (id, org_id);

create table public.deployment_runs (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  deployment_id uuid not null,
  status text not null default 'queued'
    check (status in ('queued', 'running', 'succeeded', 'failed')),
  source text not null default 'branch' check (source in ('branch')),
  branch text,
  commit_sha text,
  log text not null default '',
  started_by uuid not null,
  started_by_email text not null default '',
  started_at timestamptz,
  finished_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (id, org_id),
  foreign key (deployment_id, org_id)
    references public.deployments (id, org_id) on delete cascade
);

create index deployment_runs_org_idx on public.deployment_runs (org_id);
create index deployment_runs_deployment_idx
  on public.deployment_runs (deployment_id, created_at desc);

-- Single-flight (US-1.32): a deployment can never have two live runs.
-- Different deployments may run concurrently.
create unique index deployment_runs_single_flight
  on public.deployment_runs (deployment_id)
  where status in ('queued', 'running');

alter table public.deployment_runs enable row level security;

create policy "members read their org deployment runs"
  on public.deployment_runs for select
  using (public.is_org_member(org_id));

create trigger deployment_runs_touch
  before update on public.deployment_runs
  for each row execute function public.touch_updated_at();

create table public.deployment_run_events (
  id bigint generated always as identity primary key,
  org_id uuid not null references public.organizations(id) on delete cascade,
  run_id uuid not null,
  phase text not null,
  message text not null default '',
  data jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  foreign key (run_id, org_id)
    references public.deployment_runs (id, org_id) on delete cascade
);

create index deployment_run_events_run_idx
  on public.deployment_run_events (run_id, id);
create index deployment_run_events_org_idx on public.deployment_run_events (org_id);

alter table public.deployment_run_events enable row level security;

create policy "members read their org deployment run events"
  on public.deployment_run_events for select
  using (public.is_org_member(org_id));

-- Live run view (US-1.32): stream inserts/updates to the browser.
alter table public.deployment_runs replica identity full;
alter publication supabase_realtime add table public.deployment_runs;
alter table public.deployment_run_events replica identity full;
alter publication supabase_realtime add table public.deployment_run_events;
