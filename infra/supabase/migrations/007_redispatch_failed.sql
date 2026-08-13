-- 007_redispatch_failed: a failed task is retryable (US-1.10 — a crashed or
-- interrupted run must not dead-end the task). dispatch_task v3 accepts
-- 'failed' alongside 'draft' and 'needs-fixes'; everything else is unchanged.

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
