-- 008_test_cases: test case management (US-1.16).
-- A per-project library of test cases (human- or agent-written), plus
-- persistent test-run sessions recording pass/fail per test per environment.

create table public.test_cases (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  project_id uuid not null references public.projects(id) on delete cascade,
  task_id uuid references public.tasks(id) on delete set null,
  title text not null,
  steps text not null default '',
  expected_result text not null default '',
  source text not null default 'human' check (source in ('human', 'agent')),
  test_types jsonb not null default '[]'::jsonb,
  environments jsonb not null default '[]'::jsonb,
  status text not null default 'active' check (status in ('active', 'abandoned')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index test_cases_project_idx on public.test_cases (project_id, status, created_at desc);
create index test_cases_task_idx on public.test_cases (task_id);

-- A run session: selection is frozen into test_run_results at start, each
-- click saves immediately, so a run survives leaving and coming back.
create table public.test_runs (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  project_id uuid not null references public.projects(id) on delete cascade,
  environment text not null,
  label text not null default '',
  status text not null default 'in-progress' check (status in ('in-progress', 'completed')),
  started_by uuid not null references auth.users(id),
  created_at timestamptz not null default now(),
  completed_at timestamptz
);

create index test_runs_project_idx on public.test_runs (project_id, created_at desc);

create table public.test_run_results (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  test_run_id uuid not null references public.test_runs(id) on delete cascade,
  test_case_id uuid not null references public.test_cases(id) on delete cascade,
  result text not null default 'pending' check (result in ('pending', 'pass', 'fail', 'skipped')),
  note text,
  recorded_at timestamptz,
  unique (test_run_id, test_case_id)
);

create index test_run_results_run_idx on public.test_run_results (test_run_id);

alter table public.test_cases enable row level security;
alter table public.test_runs enable row level security;
alter table public.test_run_results enable row level security;

create policy "members manage their org test cases"
  on public.test_cases for all
  using (public.is_org_member(org_id))
  with check (public.is_org_member(org_id));

create policy "members manage their org test runs"
  on public.test_runs for all
  using (public.is_org_member(org_id))
  with check (public.is_org_member(org_id));

create policy "members manage their org test run results"
  on public.test_run_results for all
  using (public.is_org_member(org_id))
  with check (public.is_org_member(org_id));

create trigger test_cases_updated_at
  before update on public.test_cases
  for each row execute function public.touch_updated_at();
