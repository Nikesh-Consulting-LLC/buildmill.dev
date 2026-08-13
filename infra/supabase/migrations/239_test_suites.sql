-- 239_test_suites: a project declares its test suites (US-81.1).
--
-- The factory has test *cases* — prose a human reads — but no concept of an
-- automated suite: a command that runs specs from the project's own repo
-- against a deployed instance and emits JUnit XML. This migration adds the
-- declarations only. Nothing executes yet (us-81.2); declaring a suite
-- changes no behavior anywhere else.
--
-- A suite is org config, like a deployment: the definition lives here, the
-- spec files it runs live in the project repo so they pin with the release
-- commit. Cross-org integrity follows 020's composite-FK pattern.

create table public.test_suites (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  project_id uuid not null,
  name text not null,
  -- informative: what kind of specs this suite runs. The pipeline treats
  -- both identically; preflight and provisioning care.
  layer text not null check (layer in ('api', 'browser')),
  -- a /bin/sh -e script, the same contract as deployments.script
  run_command text not null default '',
  -- where the JUnit XML lands, relative to the checkout
  results_path text not null default 'test-results/junit.xml',
  -- null = run on the target deployment's own server, the one machine
  -- guaranteed to reach the deployment's website_url (the health_check_once
  -- precedent). Set it to run from a dedicated registered test box instead.
  server_id uuid,
  run_on_uat boolean not null default true,
  -- the prod-safe smoke subset (us-82.1). Off by default: tests against
  -- live production data are opted into, never assumed.
  run_on_prod boolean not null default false,
  -- US-81.4: whether a failing run blocks release sign-off. Default false —
  -- automated results are advisory until a person decides they gate.
  blocks_signoff boolean not null default false,
  timeout_minutes int not null default 30
    check (timeout_minutes between 1 and 720),
  status text not null default 'active'
    check (status in ('active', 'archived')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (project_id, name),
  -- for composite FKs pointing here (test_cases below, suite_runs in 240)
  unique (id, org_id),
  foreign key (project_id, org_id)
    references public.projects (id, org_id) on delete cascade,
  -- a server with suites pointing at it refuses to die, like deployments
  foreign key (server_id, org_id)
    references public.servers (id, org_id) on delete restrict
);

comment on table public.test_suites is
  'US-81.1: a declared automated test suite — a command that runs repo specs '
  'against a deployed instance and emits JUnit XML. Declaration only; '
  'execution is us-81.2.';

create index test_suites_org_idx on public.test_suites (org_id);
create index test_suites_project_idx on public.test_suites (project_id);
create index test_suites_server_idx on public.test_suites (server_id);

alter table public.test_suites enable row level security;

create policy "members manage their org test suites"
  on public.test_suites for all
  using (public.is_org_member(org_id))
  with check (public.is_org_member(org_id));

create trigger test_suites_touch
  before update on public.test_suites
  for each row execute function public.touch_updated_at();

-- ---------------------------------------------------------------------------
-- A case can be automated: it names the suite that answers it and the stable
-- JUnit identity of its spec (a pytest nodeid, or Playwright's file::title).
-- `always_on_uat` is the person-flagged "this runs on every release" mark —
-- us-81.5 teaches release inheritance to honor it.
-- ---------------------------------------------------------------------------

alter table public.test_cases
  add column execution text not null default 'manual'
    check (execution in ('manual', 'automated')),
  add column suite_id uuid,
  add column spec_ref text,
  add column always_on_uat boolean not null default false;

-- set null (suite_id) only — a bare SET NULL would null org_id, which is NOT NULL.
alter table public.test_cases
  add constraint test_cases_suite_fk foreign key (suite_id, org_id)
  references public.test_suites (id, org_id) on delete set null (suite_id);

create index test_cases_suite_idx on public.test_cases (suite_id)
  where suite_id is not null;

comment on column public.test_cases.spec_ref is
  'US-81.1: stable JUnit identity of the spec that answers this case — '
  'classname::name as the suite''s report emits it. With suite_id, the '
  'two-way link between the case library and what actually runs.';
