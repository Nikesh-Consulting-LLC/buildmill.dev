-- 215_release_system_job: release becomes a system job, not an agent
-- protocol (Phase 63).
--
-- US-21.3 made "the release run" one agent job covering notes, triggering
-- the UAT deploy, verifying health, and test cases — but that protocol only
-- existed on the MCP surface (get_release_changes/trigger_deployment/
-- get_deployment_health/submit_release_run). A git-native worker could push
-- a branch and close the run out through the generic worker submit endpoint
-- without ever writing notes or deploying, leaving the release stuck
-- in-flight with no path forward. Deploying/health-checking is also not an
-- agent judgment call — it is the exact deterministic pipeline
-- apps/api/app/deploy.py already runs for every ordinary deployment.
--
-- US-63.1: the agent's job narrows to notes only.
-- US-63.2: the resulting hand-off triggers deploy.py's pipeline directly.
-- US-63.3: release prep moves off runs onto its own lightweight task.

-- ---------------------------------------------------------------------------
-- US-63.1/63.2: the split lifecycle needs states between "an agent is
-- writing notes" and "deployed" — deploying is now its own tracked phase,
-- not folded into a single agent-reported jump.
-- ---------------------------------------------------------------------------

alter table public.releases drop constraint if exists releases_status_check;
alter table public.releases add constraint releases_status_check
  check (status in (
    'queued',            -- release-prep is queued
    'running',           -- an agent holds release-prep
    'notes-ready',       -- notes written; UAT deploy about to fire
    'deploying',         -- the UAT deploy pipeline is in flight
    'uat-deployed',      -- deployed to UAT, health verified
    'uat-deploy-failed', -- the UAT deploy pipeline failed; still in-flight
    'uat-signed-off',    -- every test case passed and the manager signed off
    'promoting',         -- the production deploy is in flight
    'released',          -- live in production
    'rolled-back',       -- was released, then rolled back
    'rejected',          -- failed UAT; superseded by a later release
    'cancelled',         -- withdrawn before an agent held it
    'failed'             -- release-prep itself failed
  ));

drop index if exists public.releases_one_in_flight_per_project;
create unique index releases_one_in_flight_per_project
  on public.releases (project_id)
  where status in (
    'queued', 'running', 'notes-ready', 'deploying', 'uat-deployed',
    'uat-deploy-failed', 'uat-signed-off', 'promoting'
  );

comment on index public.releases_one_in_flight_per_project is
  'US-63.2 widened the in-flight set from migration 130: deploying and '
  'uat-deploy-failed both still need a next action, so a release stuck '
  'there still blocks a fresh cut rather than silently vanishing.';

-- US-63.2: the pipeline's own completion (apps/api/app/deploy.py, in
-- run_pipeline's four terminal branches) needs to know which release
-- (if any) to update — without deploy.py knowing anything about releases
-- beyond this one nullable pointer.
alter table public.deployment_runs
  add column if not exists release_id uuid
    references public.releases(id) on delete set null;

comment on column public.deployment_runs.release_id is
  'US-63.2: set only when this deploy run was triggered by a release '
  'hand-off (not a manual/agent-dispatched run). The pipeline reads it at '
  'its own terminal points to flip releases.status forward.';

-- ---------------------------------------------------------------------------
-- US-63.3: release prep gets its own lightweight task
-- ---------------------------------------------------------------------------
-- Not another runs.kind: issue fan-out is a no-op for it (issue-less by
-- design), run_attempts/turn-limit/resume machinery solves problems this job
-- doesn't have, and it has no business in the story-shaped Work Items pool.

create table public.release_prep_runs (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  project_id uuid not null references public.projects(id) on delete cascade,
  release_id uuid not null references public.releases(id) on delete cascade,

  status text not null default 'queued'
    check (status in ('queued', 'running', 'succeeded', 'failed', 'cancelled')),

  worker_id uuid references public.workers(id) on delete set null,
  claimed_at timestamptz,
  claim_expires_at timestamptz,

  notes_summary text,
  notes_detail text,
  error text,

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  finished_at timestamptz
);

comment on table public.release_prep_runs is
  'US-63.3: the whole of a release agent job now is "read the commit range, '
  'write notes" - its own minimal claim/submit contract, exposed identically '
  'on both the MCP and plain-HTTP worker transports, so neither can silently '
  'complete past requirements the other enforces.';

create index release_prep_runs_project_idx
  on public.release_prep_runs (project_id, created_at desc);
create index release_prep_runs_release_idx
  on public.release_prep_runs (release_id);
create index release_prep_runs_pool_idx
  on public.release_prep_runs (org_id, status) where status = 'queued';

alter table public.release_prep_runs enable row level security;

create policy "members manage their org release prep runs"
  on public.release_prep_runs for all
  using (public.is_org_member(org_id))
  with check (public.is_org_member(org_id));

create trigger release_prep_runs_touch
  before update on public.release_prep_runs
  for each row execute function public.touch_updated_at();

alter publication supabase_realtime add table public.release_prep_runs;
