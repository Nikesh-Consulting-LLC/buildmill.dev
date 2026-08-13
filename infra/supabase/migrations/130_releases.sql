-- 130_releases: the release entity (US-21.1).
--
-- Replaces three loosely-coupled things that all called themselves "release":
-- release_records (per WORK ITEM, born at merge), release_versions (V<epic>.<seq>
-- against a branch head), and the deployment machinery that back-filled
-- `deployed` events by matching commit SHAs. Nothing enforced an order between
-- them — QA could be signed off on an environment nothing was deployed to.
--
-- A release is cut from the default branch at any time and PINS that commit.
-- Everything downstream — notes, the UAT deploy, the promotion to production —
-- reads `commit_sha`, never "main now": between cutting a release and an agent
-- claiming it, main moves, and without the pin the notes would describe one
-- build while the deploy shipped another.
--
-- The old tables are NOT dropped here. us-21.7 retires them, deliberately last,
-- so the app is never left without a release path mid-phase.

create table if not exists public.releases (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  project_id uuid not null references public.projects(id) on delete cascade,

  -- YYYY.MM.DD.N by default, overridable at creation, unique per project.
  version text not null,
  -- The pinned commit. Immutable for the life of the release.
  commit_sha text not null,
  git_tag text,
  previous_release_id uuid references public.releases(id) on delete set null,

  status text not null default 'queued' check (status in (
    'queued',        -- the release run is queued
    'running',       -- an agent holds it
    'uat-deployed',  -- notes written, deployed to UAT, health verified
    'uat-signed-off',-- every test case passed and the manager signed off
    'promoting',     -- the production deploy is in flight
    'released',      -- live in production
    'rolled-back',   -- was released, then rolled back
    'rejected',      -- failed UAT; superseded by a later release
    'failed'         -- the release run or its deployment failed
  )),

  -- Snapshot, not a live join: a later edit to a work item must not rewrite
  -- what a shipped release claims to contain.
  included_items jsonb not null default '[]'::jsonb,

  notes_summary text,
  notes_detail text,

  uat_deployment_run_id uuid,
  prod_deployment_run_id uuid,

  -- Milestones, for the timeline and for resuming a re-dispatched run.
  notes_written_at timestamptz,
  uat_deployed_at timestamptz,
  cases_attached_at timestamptz,
  signed_off_at timestamptz,
  signed_off_by uuid,
  promoted_at timestamptz,
  promoted_by uuid,
  released_at timestamptz,
  rolled_back_at timestamptz,
  rejected_at timestamptz,
  rejected_reason text,
  failure_reason text,

  created_by uuid default auth.uid(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),

  unique (project_id, version)
);

comment on table public.releases is
  'US-21.1: one build, cut from the default branch and pinned to a commit. The '
  'only thing that knows what shipped — work items are not linked to releases '
  'and end at merged.';
comment on column public.releases.commit_sha is
  'Pinned at creation. Notes, the UAT deploy and the promotion all read this, '
  'never the branch head at the time they run.';
comment on column public.releases.included_items is
  'Snapshot of the work items merged since the previous release, resolved from '
  'runs.merge_commit_sha over the GitHub commit range.';

create index if not exists releases_project_idx
  on public.releases (project_id, created_at desc);
create index if not exists releases_org_idx
  on public.releases (org_id, created_at desc);

alter table public.releases enable row level security;

drop policy if exists "members read their org releases" on public.releases;
create policy "members read their org releases"
  on public.releases for select
  using (public.is_org_member(org_id));

drop policy if exists "members cut releases" on public.releases;
create policy "members cut releases"
  on public.releases for insert
  with check (public.is_org_member(org_id));

drop policy if exists "members update their org releases" on public.releases;
create policy "members update their org releases"
  on public.releases for update
  using (public.is_org_member(org_id))
  with check (public.is_org_member(org_id));

-- A release is a permanent record. Rejecting or rolling one back is a status,
-- never a delete — so there is no delete policy.

alter publication supabase_realtime add table public.releases;

-- ---------------------------------------------------------------------------
-- Which deployment does a release ship to? (US-21.1)
-- ---------------------------------------------------------------------------
-- A project can have several UAT deployments. Without a designation the agent
-- would pick one arbitrarily.

alter table public.projects
  add column if not exists release_uat_deployment_id uuid
    references public.deployments(id) on delete set null,
  add column if not exists release_prod_deployment_id uuid
    references public.deployments(id) on delete set null;

comment on column public.projects.release_uat_deployment_id is
  'US-21.1: the deployment every release is shipped to first. Required to cut '
  'a release.';
comment on column public.projects.release_prod_deployment_id is
  'US-21.5: where a signed-off release is promoted to.';

-- ---------------------------------------------------------------------------
-- The proposed version: the cut date plus a same-day counter
-- ---------------------------------------------------------------------------
-- Date-based rather than V<epic>.<seq>: that scheme assumed an active epic was
-- the versioning root, and a release cut from main spans epics. The manager may
-- override the proposal, but the AGENT never chooses — it reads the version off
-- the release row (us-7.14's "read, never chosen" rule, with its source moved).

create or replace function public.next_release_version(p_project uuid)
returns text
language plpgsql
stable
as $$
declare
  v_day text := to_char(now() at time zone 'utc', 'YYYY.MM.DD');
  v_n int;
begin
  select coalesce(max(split_part(r.version, '.', 4)::int), 0) + 1
    into v_n
  from public.releases r
  where r.project_id = p_project
    and r.version like v_day || '.%'
    -- Ignore a manually-overridden version that does not fit the scheme, so
    -- one hand-typed name cannot break the counter for the rest of the day.
    and split_part(r.version, '.', 4) ~ '^[0-9]+$';
  return v_day || '.' || v_n;
end;
$$;

grant execute on function public.next_release_version(uuid) to authenticated;

-- ---------------------------------------------------------------------------
-- One release in flight per project
-- ---------------------------------------------------------------------------
-- Everything before `released`/`rejected`/`rolled-back` is in flight. A partial
-- unique index is the honest place for this: two managers cutting at once must
-- not both succeed.

create unique index if not exists releases_one_in_flight_per_project
  on public.releases (project_id)
  where status in ('queued', 'running', 'uat-deployed', 'uat-signed-off', 'promoting');
