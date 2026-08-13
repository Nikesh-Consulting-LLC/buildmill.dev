-- 103_runner_incidents: runner-fault events & health (US-10.11).
--
-- A failed run is tagged work-fault (the story/plan/code is wrong) or runner-fault
-- (the machine/environment is broken — bad clone, disk full, missing CLI). Only
-- runner-faults land here, distinct from per-run work failures, so a chronically
-- broken runner is visible and fixable. Written by the API service role on submit;
-- org members read their runners' incidents. Health is derived from recent
-- incidents (see db.runner_health).

create table public.runner_incidents (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  worker_id uuid not null references public.workers(id) on delete cascade,
  run_id uuid,
  kind text not null default 'runner-fault',
  message text,
  created_at timestamptz not null default now()
);

create index runner_incidents_worker_idx
  on public.runner_incidents (worker_id, created_at desc);

alter table public.runner_incidents enable row level security;

create policy "org members read runner incidents"
  on public.runner_incidents for select
  using (public.is_org_member(org_id));

alter publication supabase_realtime add table public.runner_incidents;
