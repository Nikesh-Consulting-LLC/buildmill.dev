-- 102_runner_command_audit: audited, policy-gated runner shell (US-10.7).
--
-- The supervisor runner may run any shell command (full autonomy), but every
-- command is checked against the runner's autonomy policy and RECORDED here
-- before it runs, with its exit + output filled in after. Org members read
-- their runners' command trail; rows are written only by the API service role
-- (the runner asks over the control socket, US-10.1). Policy can flip a runner
-- to deny/require-approval as a kill switch.

create table public.runner_command_audit (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  worker_id uuid not null references public.workers(id) on delete cascade,
  session_id uuid,
  run_id uuid,
  argv text[] not null default '{}',
  cwd text,
  policy_decision text not null default 'allow',
  started_at timestamptz not null default now(),
  finished_at timestamptz,
  exit_code int,
  output text
);

create index runner_command_audit_worker_idx
  on public.runner_command_audit (worker_id, started_at desc);
create index runner_command_audit_run_idx
  on public.runner_command_audit (run_id) where run_id is not null;

alter table public.runner_command_audit enable row level security;

create policy "org members read runner command audit"
  on public.runner_command_audit for select
  using (public.is_org_member(org_id));

alter publication supabase_realtime add table public.runner_command_audit;
