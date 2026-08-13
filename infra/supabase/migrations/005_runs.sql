-- 005_runs: provider runs + transactional dispatch (US-1.9).
-- A run is one provider execution for a task; input_context is the exact
-- snapshot the runner will receive.

create table public.runs (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  task_id uuid not null references public.tasks(id) on delete cascade,
  provider text not null default 'claude',
  status text not null default 'queued'
    check (status in ('queued', 'running', 'succeeded', 'failed')),
  input_context jsonb not null,
  stdout text,
  branch_ref text,
  pr_url text,
  tokens_in integer,
  tokens_out integer,
  cost_usd numeric(10, 4),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  started_at timestamptz,
  finished_at timestamptz
);

create index runs_task_idx on public.runs (task_id, created_at);
create index runs_org_idx on public.runs (org_id);
create index runs_active_idx on public.runs (status) where status in ('queued', 'running');

alter table public.runs enable row level security;

create policy "members manage their org runs"
  on public.runs for all
  using (public.is_org_member(org_id))
  with check (public.is_org_member(org_id));

create trigger runs_touch
  before update on public.runs
  for each row execute function public.touch_updated_at();

-- Transactional dispatch: validate -> snapshot context -> create run ->
-- queue task -> log event. SECURITY INVOKER: authorization is RLS —
-- a non-member simply cannot see the task ("task not found").
create or replace function public.dispatch_task(p_task uuid)
returns uuid
language plpgsql
as $$
declare
  v_task public.tasks%rowtype;
  v_project public.projects%rowtype;
  v_run uuid;
begin
  select * into v_task from public.tasks where id = p_task for update;
  if not found then
    raise exception 'task not found';
  end if;
  if v_task.status not in ('draft', 'needs-fixes') then
    raise exception 'task is not dispatchable from status "%"', v_task.status;
  end if;

  select * into v_project from public.projects where id = v_task.project_id;

  insert into public.runs (org_id, task_id, provider, status, input_context)
  values (
    v_task.org_id, p_task, 'claude', 'queued',
    jsonb_build_object(
      'title', v_task.title,
      'story', v_task.story,
      'acceptance_criteria', v_task.acceptance_criteria,
      'repo_full_name', v_project.repo_full_name,
      'default_branch', v_project.default_branch
    )
  )
  returning id into v_run;

  update public.tasks set status = 'queued' where id = p_task;

  insert into public.task_events (org_id, task_id, type, payload)
  values (v_task.org_id, p_task, 'dispatched', jsonb_build_object('run_id', v_run));

  return v_run;
end;
$$;

revoke execute on function public.dispatch_task(uuid) from public, anon;
grant execute on function public.dispatch_task(uuid) to authenticated;
