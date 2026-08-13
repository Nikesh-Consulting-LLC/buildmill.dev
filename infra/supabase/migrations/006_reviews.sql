-- 006_reviews: manager review decisions (US-1.12/1.13), diff/error storage
-- on runs (simulated provider keeps the diff in-db), and a feedback-aware
-- dispatch_task so retries are informed (US-1.13).

alter table public.runs add column diff text;
alter table public.runs add column error text;

create table public.reviews (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  run_id uuid not null references public.runs(id) on delete cascade,
  decision text not null check (decision in ('approved', 'rejected')),
  comment text,
  reviewer uuid not null references auth.users(id),
  created_at timestamptz not null default now()
);

create index reviews_run_idx on public.reviews (run_id, created_at);

alter table public.reviews enable row level security;

create policy "members manage their org reviews"
  on public.reviews for all
  using (public.is_org_member(org_id))
  with check (public.is_org_member(org_id));

-- v2: a retry after rejection carries the rejection comment and the prior
-- branch/PR so the provider continues informed, on the same branch.
create or replace function public.dispatch_task(p_task uuid)
returns uuid
language plpgsql
as $$
declare
  v_task public.tasks%rowtype;
  v_project public.projects%rowtype;
  v_prev public.runs%rowtype;
  v_feedback text;
  v_context jsonb;
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

  v_context := jsonb_build_object(
    'title', v_task.title,
    'story', v_task.story,
    'acceptance_criteria', v_task.acceptance_criteria,
    'repo_full_name', v_project.repo_full_name,
    'default_branch', v_project.default_branch
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

-- Approve: record the decision and mark merged. The PR merge itself happens
-- in the API before this commits (simulated for simulated PRs).
create or replace function public.approve_run(p_run uuid)
returns void
language plpgsql
as $$
declare
  v_run public.runs%rowtype;
  v_task public.tasks%rowtype;
begin
  select * into v_run from public.runs where id = p_run for update;
  if not found then
    raise exception 'run not found';
  end if;
  select * into v_task from public.tasks where id = v_run.task_id for update;
  if v_task.status <> 'in-review' then
    raise exception 'task is not in review (status "%")', v_task.status;
  end if;

  insert into public.reviews (org_id, run_id, decision, reviewer)
  values (v_run.org_id, p_run, 'approved', auth.uid());

  update public.tasks set status = 'merged' where id = v_task.id;

  insert into public.task_events (org_id, task_id, type, payload)
  values
    (v_run.org_id, v_task.id, 'approved', jsonb_build_object('run_id', p_run)),
    (v_run.org_id, v_task.id, 'merged',
     jsonb_build_object('run_id', p_run, 'pr_url', v_run.pr_url));
end;
$$;

-- Reject: comment is required — it becomes the feedback of the next run.
create or replace function public.reject_run(p_run uuid, p_comment text)
returns void
language plpgsql
as $$
declare
  v_run public.runs%rowtype;
  v_task public.tasks%rowtype;
begin
  if p_comment is null or length(trim(p_comment)) = 0 then
    raise exception 'a comment is required to reject';
  end if;

  select * into v_run from public.runs where id = p_run for update;
  if not found then
    raise exception 'run not found';
  end if;
  select * into v_task from public.tasks where id = v_run.task_id for update;
  if v_task.status <> 'in-review' then
    raise exception 'task is not in review (status "%")', v_task.status;
  end if;

  insert into public.reviews (org_id, run_id, decision, comment, reviewer)
  values (v_run.org_id, p_run, 'rejected', p_comment, auth.uid());

  update public.tasks set status = 'needs-fixes' where id = v_task.id;

  insert into public.task_events (org_id, task_id, type, payload)
  values (v_run.org_id, v_task.id, 'rejected',
          jsonb_build_object('run_id', p_run, 'comment', p_comment));
end;
$$;

revoke execute on function public.approve_run(uuid) from public, anon;
grant execute on function public.approve_run(uuid) to authenticated;
revoke execute on function public.reject_run(uuid, text) from public, anon;
grant execute on function public.reject_run(uuid, text) to authenticated;
