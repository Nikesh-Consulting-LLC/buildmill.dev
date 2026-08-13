-- 012_project_learnings: freeform, LLM-maintained project knowledge (US-1.21).
-- One row per project (unlike project_guidelines' section catalog) — a
-- single markdown blob a manager can edit by hand or the factory merges
-- new context into via POST /api/v1/llm/learnings/{project_id}/update.

create table public.project_learnings (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  project_id uuid not null unique references public.projects(id) on delete cascade,
  content text not null default '',
  last_updated_by text not null default 'user' check (last_updated_by in ('user', 'llm')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

alter table public.project_learnings enable row level security;

create policy "members manage their org project learnings"
  on public.project_learnings for all
  using (public.is_org_member(org_id))
  with check (public.is_org_member(org_id));

create trigger project_learnings_updated_at
  before update on public.project_learnings
  for each row execute function public.touch_updated_at();

-- Mirrors assemble_project_guidelines (009): one shared function so the
-- learnings.md endpoint and dispatch_task's input_context never diverge.
create or replace function public.assemble_project_learnings(p_project uuid)
returns text
language sql
stable
as $$
  select coalesce(
    (select content from public.project_learnings where project_id = p_project),
    ''
  );
$$;

-- dispatch_task v5: bundles project learnings alongside guidelines in
-- input_context. Everything else unchanged from v4 (009_project_guidelines.sql).
create or replace function public.dispatch_task(p_task uuid)
returns uuid
language plpgsql
as $$
declare
  v_task public.tasks%rowtype;
  v_project public.projects%rowtype;
  v_prev public.runs%rowtype;
  v_feedback text;
  v_guidelines text;
  v_learnings text;
  v_context jsonb;
  v_run uuid;
begin
  select * into v_task from public.tasks where id = p_task for update;
  if not found then
    raise exception 'task not found';
  end if;
  if v_task.status not in ('draft', 'needs-fixes', 'failed') then
    raise exception 'task is not dispatchable from status "%"', v_task.status;
  end if;

  select * into v_project from public.projects where id = v_task.project_id;

  select * into v_prev
  from public.runs
  where task_id = p_task
  order by created_at desc
  limit 1;

  if v_prev.id is not null then
    select r.comment into v_feedback
    from public.reviews r
    where r.run_id = v_prev.id and r.decision = 'rejected'
    order by r.created_at desc
    limit 1;
  end if;

  v_guidelines := public.assemble_project_guidelines(v_task.project_id);
  v_learnings := public.assemble_project_learnings(v_task.project_id);

  v_context := jsonb_build_object(
    'title', v_task.title,
    'story', v_task.story,
    'acceptance_criteria', v_task.acceptance_criteria,
    'repo_full_name', v_project.repo_full_name,
    'default_branch', v_project.default_branch,
    'guidelines', v_guidelines,
    'learnings', v_learnings
  );

  if v_feedback is not null then
    v_context := v_context || jsonb_build_object(
      'feedback', v_feedback,
      'previous_branch', v_prev.branch_ref,
      'previous_pr_url', v_prev.pr_url
    );
  end if;

  insert into public.runs (org_id, task_id, provider, status, input_context)
  values (v_task.org_id, p_task, 'claude', 'queued', v_context)
  returning id into v_run;

  update public.tasks set status = 'queued' where id = p_task;

  insert into public.task_events (org_id, task_id, type, payload)
  values (v_task.org_id, p_task, 'dispatched', jsonb_build_object('run_id', v_run));

  return v_run;
end;
$$;
