-- 240_suite_runs: the suite pipeline's run records (US-81.2).
--
-- One suite_runs row per execution of a declared suite against a deployed
-- instance — the deployment_runs shape, because it is the same kind of thing:
-- a deterministic server-side pipeline the API drives and members watch.
-- Writes are API-only (service role); members read; realtime streams both the
-- run and its event feed so the run view is live.
--
-- Terminal semantics matter to the sign-off gate (us-81.4), so they are a
-- vocabulary, not a boolean: `succeeded`/`failed` mean the JUnit report was
-- parsed (zero / nonzero failures — the report is truth, the script's exit
-- code is informative only); `error` means the factory could not test at all
-- (SSH, preflight, missing report, parse), which is not the same thing as
-- tests failing; `timed-out` is the wall clock.

create table public.suite_runs (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  project_id uuid not null,
  suite_id uuid not null,
  -- the deployment under test — where base_url came from
  deployment_id uuid not null,
  -- set when a release triggered it; a manually-triggered run has none.
  -- Plain FK like test_cases.release_id (131); the release's disappearance
  -- must not erase the fact that tests ran.
  release_id uuid references public.releases(id) on delete set null,
  trigger text not null check (trigger in ('uat-deploy', 'prod-promote', 'manual')),
  commit_sha text not null,
  base_url text not null,
  status text not null default 'queued'
    check (status in ('queued', 'running', 'succeeded', 'failed', 'error',
                      'timed-out', 'cancelled')),
  tests_total int,
  tests_passed int,
  tests_failed int,
  tests_skipped int,
  log text not null default '',
  error text,
  -- US-81.4: a manager may waive a non-succeeded verdict for sign-off. The
  -- waiver lives on the RUN: a re-run produces a fresh, unwaived verdict.
  waived_at timestamptz,
  waived_by uuid,
  waive_reason text,
  started_at timestamptz,
  finished_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (id, org_id),
  foreign key (suite_id, org_id)
    references public.test_suites (id, org_id) on delete cascade,
  foreign key (deployment_id, org_id)
    references public.deployments (id, org_id) on delete cascade,
  foreign key (project_id, org_id)
    references public.projects (id, org_id) on delete cascade
);

comment on table public.suite_runs is
  'US-81.2: one execution of a declared test suite against a deployed '
  'instance. JUnit is truth: succeeded/failed mean the report parsed; '
  'error means the factory could not test, which reads differently.';

create index suite_runs_org_idx on public.suite_runs (org_id);
create index suite_runs_suite_idx on public.suite_runs (suite_id, created_at desc);
create index suite_runs_release_idx on public.suite_runs (release_id)
  where release_id is not null;

-- One in-flight run per suite: a second trigger waits its turn.
create unique index suite_runs_single_flight
  on public.suite_runs (suite_id)
  where status in ('queued', 'running');

alter table public.suite_runs enable row level security;

create policy "members read their org suite runs"
  on public.suite_runs for select
  using (public.is_org_member(org_id));
-- No insert/update policies: the pipeline (service role) is the only writer.

create trigger suite_runs_touch
  before update on public.suite_runs
  for each row execute function public.touch_updated_at();

-- ---------------------------------------------------------------------------
-- Per-test outcomes, parsed from the JUnit report. `test_case_id` is set when
-- (suite_id, spec_ref) matches a case (us-81.4); rows with none are the
-- "untracked" tests us-82.4 adopts.
-- ---------------------------------------------------------------------------

create table public.suite_run_tests (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  suite_run_id uuid not null,
  spec_ref text not null,
  status text not null check (status in ('pass', 'fail', 'skipped', 'error')),
  duration_ms int,
  message text,
  test_case_id uuid references public.test_cases(id) on delete set null,
  created_at timestamptz not null default now(),
  foreign key (suite_run_id, org_id)
    references public.suite_runs (id, org_id) on delete cascade
);

create index suite_run_tests_run_idx on public.suite_run_tests (suite_run_id);
create index suite_run_tests_org_idx on public.suite_run_tests (org_id);

alter table public.suite_run_tests enable row level security;

create policy "members read their org suite run tests"
  on public.suite_run_tests for select
  using (public.is_org_member(org_id));

-- ---------------------------------------------------------------------------
-- The live phase feed — deployment_run_events' exact shape.
-- ---------------------------------------------------------------------------

create table public.suite_run_events (
  id bigint generated always as identity primary key,
  org_id uuid not null references public.organizations(id) on delete cascade,
  run_id uuid not null,
  phase text not null,
  message text not null default '',
  data jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  foreign key (run_id, org_id)
    references public.suite_runs (id, org_id) on delete cascade
);

create index suite_run_events_run_idx on public.suite_run_events (run_id, id);
create index suite_run_events_org_idx on public.suite_run_events (org_id);

alter table public.suite_run_events enable row level security;

create policy "members read their org suite run events"
  on public.suite_run_events for select
  using (public.is_org_member(org_id));

alter publication supabase_realtime add table public.suite_runs;
alter publication supabase_realtime add table public.suite_run_events;
