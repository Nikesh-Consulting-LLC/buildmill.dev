-- 120_run_prev_issue_status: record each run's pre-dispatch issue status (US-15.14).
--
-- Resetting an in-flight run must return the issue to exactly the status it
-- had immediately before that run was dispatched. Nothing recorded that, so we
-- add runs.prev_issue_status and have every dispatch path stamp it at insert.
-- (dispatch_issue moves the issue to 'queued' for plan/code; prd/breakdown
-- leave the status alone, so for them prev == the status at dispatch.)

alter table public.runs add column if not exists prev_issue_status text;

-- dispatch_issue (from 104) — now stamps prev_issue_status = the status the
-- issue had before this run flipped it to 'queued'.
create or replace function public.dispatch_issue(p_issue uuid)
returns uuid
language plpgsql
as $$
declare
  v_issue public.issues%rowtype;
  v_project public.projects%rowtype;
  v_prev public.runs%rowtype;
  v_feedback text;
  v_context jsonb;
  v_run uuid;
  v_kind text;
  v_has_approved_plan boolean;
  v_child_count int;
  v_prd_content text;
  v_plan_content text;
  v_test_plan_content text;
  v_pre_status text;
  v_prd_issue uuid;
begin
  select * into v_issue from public.issues where id = p_issue for update;
  if not found then
    raise exception 'issue not found';
  end if;
  if v_issue.abandoned_at is not null then
    raise exception 'issue is abandoned';
  end if;

  select count(*) into v_child_count
  from public.issues
  where parent_id = p_issue and abandoned_at is null;
  if v_issue.type = 'feature' and v_child_count > 0 then
    raise exception 'feature with child stories is not dispatchable';
  end if;

  select exists(
    select 1 from public.artifacts
    where issue_id = p_issue and kind = 'plan' and status = 'approved'
  ) into v_has_approved_plan;

  if v_has_approved_plan and v_issue.status in ('planned', 'needs-fixes') then
    v_kind := 'code';
  elsif v_issue.status in ('draft', 'ready', 'failed') then
    v_kind := 'plan';
  elsif v_issue.status = 'needs-fixes' and not v_has_approved_plan then
    v_kind := 'plan';
  else
    raise exception 'issue is not dispatchable from status "%"', v_issue.status;
  end if;

  -- US-11.2: the guard this migration exists for.
  if v_issue.type = 'feature' and v_kind = 'plan' then
    raise exception 'a feature is not planned directly — approve its PRD and break it into stories, then plan those';
  end if;

  if v_kind = 'code' and not v_has_approved_plan then
    raise exception 'code run requires an approved plan';
  end if;

  select * into v_project from public.projects where id = v_issue.project_id;

  select * into v_prev
  from public.runs
  where issue_id = p_issue and kind = v_kind
  order by created_at desc
  limit 1;

  v_feedback := null;
  if v_prev.id is not null then
    if v_kind = 'code' then
      select a.comment into v_feedback
      from public.approvals a
      where a.subject_type = 'run'
        and a.subject_id = v_prev.id
        and a.gate = 'code-review'
        and a.decision = 'rejected'
      order by a.created_at desc
      limit 1;
    else
      select a.comment into v_feedback
      from public.approvals a
      where a.issue_id = p_issue
        and a.gate = 'plan'
        and a.decision = 'sent-back'
      order by a.created_at desc
      limit 1;
    end if;
  end if;

  v_prd_issue := coalesce(v_issue.parent_id, case when v_issue.type = 'feature' then v_issue.id end);

  v_context := jsonb_build_object(
    'title', v_issue.title,
    'type', v_issue.type,
    'story', v_issue.body,
    'body', v_issue.body,
    'acceptance_criteria', v_issue.acceptance_criteria,
    'repo_full_name', v_project.repo_full_name,
    'default_branch', v_project.default_branch,
    'run_kind', v_kind,
    'guidelines', public.assemble_project_guidelines(v_issue.project_id),
    'learnings', public.assemble_project_learnings(v_issue.project_id),
    'documents', coalesce((
      select jsonb_agg(jsonb_build_object(
        'id', d.id,
        'name', d.name,
        'mime_type', d.mime_type,
        'size_bytes', d.size_bytes,
        'attached_to', d.attached_to
      ) order by d.created_at)
      from public.documents d
      where (d.issue_id = p_issue and d.attached_to = 'work-item')
         or (v_prd_issue is not null
             and d.issue_id = v_prd_issue and d.attached_to = 'prd')
    ), '[]'::jsonb),
    -- US-5.7: the manager's test cases ride along so the agent's tests
    -- match what UAT will check. Active cases only.
    'test_cases', coalesce((
      select jsonb_agg(jsonb_build_object(
        'id', t.id,
        'title', t.title,
        'steps', t.steps,
        'expected_result', t.expected_result
      ) order by t.created_at)
      from public.test_cases t
      where t.issue_id = p_issue and t.status = 'active'
    ), '[]'::jsonb)
  );

  if v_prd_issue is not null then
    select a.content into v_prd_content
    from public.artifacts a
    where a.issue_id = v_prd_issue and a.kind = 'prd' and a.status = 'approved'
    order by a.version desc limit 1;
    if v_prd_content is not null then
      v_context := v_context || jsonb_build_object('prd', v_prd_content);
    end if;
  end if;

  if v_kind = 'code' then
    select a.content into v_plan_content
    from public.artifacts a
    where a.issue_id = p_issue and a.kind = 'plan' and a.status = 'approved'
    order by a.version desc limit 1;
    select a.content into v_test_plan_content
    from public.artifacts a
    where a.issue_id = p_issue and a.kind = 'test_plan' and a.status = 'approved'
    order by a.version desc limit 1;
    v_context := v_context || jsonb_build_object(
      'plan', v_plan_content,
      'test_plan', v_test_plan_content
    );
  elsif v_feedback is not null then
    select a.content into v_plan_content
    from public.artifacts a
    where a.issue_id = p_issue and a.kind = 'plan'
    order by a.version desc limit 1;
    if v_plan_content is not null then
      v_context := v_context || jsonb_build_object('previous_plan', v_plan_content);
    end if;
  end if;

  if v_feedback is not null then
    v_context := v_context || jsonb_build_object(
      'feedback', v_feedback,
      'previous_branch', v_prev.branch_ref,
      'previous_pr_url', v_prev.pr_url
    );
  end if;

  -- US-5.11: guarantee an instruction set before the run hits the pool.
  perform public.seed_issue_instructions(p_issue, v_kind);

  v_pre_status := v_issue.status;

  -- US-15.14: stamp the pre-dispatch status so a reset can restore it exactly.
  insert into public.runs (org_id, issue_id, provider, status, kind, input_context, prev_issue_status)
  values (v_issue.org_id, p_issue, 'claude', 'queued', v_kind, v_context, v_pre_status)
  returning id into v_run;

  update public.issues set status = 'queued' where id = p_issue;

  insert into public.issue_events (org_id, issue_id, type, payload)
  values (
    v_issue.org_id,
    p_issue,
    case when v_kind = 'plan' then 'plan-dispatched' else 'dispatched' end,
    jsonb_build_object('run_id', v_run, 'kind', v_kind, 'from_status', v_pre_status)
  );

  return v_run;
end;
$$;

-- dispatch_prd_draft (from 053) — a PRD run leaves issues.status alone, so
-- prev_issue_status is simply the status at dispatch.
create or replace function public.dispatch_prd_draft(p_issue uuid)
returns uuid
language plpgsql
as $$
declare
  v_issue public.issues%rowtype;
  v_prior public.artifacts%rowtype;
  v_feedback text;
  v_context jsonb;
  v_run uuid;
begin
  select * into v_issue from public.issues where id = p_issue for update;
  if not found then
    raise exception 'issue not found';
  end if;
  if v_issue.abandoned_at is not null then
    raise exception 'issue is abandoned';
  end if;
  if v_issue.type <> 'feature' then
    raise exception 'PRDs are only for feature issues';
  end if;
  if v_issue.status not in ('draft', 'prd-review', 'ready') then
    raise exception 'cannot draft PRD from status "%"', v_issue.status;
  end if;

  select * into v_prior
  from public.artifacts
  where issue_id = p_issue and kind = 'prd'
  order by version desc limit 1;

  if v_prior.id is not null then
    select a.comment into v_feedback
    from public.approvals a
    where a.issue_id = p_issue and a.gate = 'prd' and a.decision = 'sent-back'
    order by a.created_at desc limit 1;
  end if;

  v_context := jsonb_build_object(
    'title', v_issue.title,
    'type', v_issue.type,
    'story', v_issue.body,
    'body', v_issue.body,
    'run_kind', 'prd',
    'guidelines', public.assemble_project_guidelines(v_issue.project_id),
    'learnings', public.assemble_project_learnings(v_issue.project_id)
  );
  if v_prior.id is not null then
    v_context := v_context || jsonb_build_object('previous_prd', v_prior.content);
  end if;
  if v_feedback is not null then
    v_context := v_context || jsonb_build_object('feedback', v_feedback);
  end if;

  -- US-5.11: guarantee an instruction set before the run hits the pool.
  perform public.seed_issue_instructions(p_issue, 'prd');

  insert into public.runs (org_id, issue_id, provider, status, kind, input_context, prev_issue_status)
  values (v_issue.org_id, p_issue, 'claude', 'queued', 'prd', v_context, v_issue.status)
  returning id into v_run;

  insert into public.issue_events (org_id, issue_id, type, payload)
  values (v_issue.org_id, p_issue, 'prd-dispatched', jsonb_build_object('run_id', v_run));

  return v_run;
end;
$$;

-- dispatch_breakdown (from 119) — a breakdown run leaves issues.status alone
-- too, so prev_issue_status == the status at dispatch.
create or replace function public.dispatch_breakdown(p_issue uuid)
returns uuid
language plpgsql
as $$
declare
  v_issue public.issues%rowtype;
  v_prd public.artifacts%rowtype;
  v_children int;
  v_context jsonb;
  v_run uuid;
begin
  select * into v_issue from public.issues where id = p_issue for update;
  if not found then
    raise exception 'issue not found';
  end if;
  if v_issue.abandoned_at is not null then
    raise exception 'issue is abandoned';
  end if;
  if v_issue.type <> 'feature' then
    raise exception 'only a feature can be broken into stories';
  end if;
  if v_issue.status <> 'ready' then
    raise exception 'only a ready feature can be broken into stories';
  end if;

  select * into v_prd
  from public.artifacts
  where issue_id = p_issue and kind = 'prd' and status = 'approved'
  order by version desc limit 1;
  if v_prd.id is null then
    raise exception 'approved PRD required';
  end if;

  -- US-15.9: a breakdown already queued, running, or succeeded means this
  -- feature is being (or has been) split — a second run would double the
  -- children.
  perform 1 from public.runs
  where issue_id = p_issue and kind = 'breakdown'
    and status in ('queued', 'running', 'succeeded');
  if found then
    raise exception 'a breakdown run is already in progress or complete for this feature';
  end if;

  select count(*) into v_children
  from public.issues where parent_id = p_issue;
  if v_children > 0 then
    raise exception 'feature already has children — use Add story instead';
  end if;

  v_context := jsonb_build_object(
    'title', v_issue.title,
    'type', v_issue.type,
    'story', v_issue.body,
    'body', v_issue.body,
    'run_kind', 'breakdown',
    'prd', v_prd.content,
    'breakdown_mode', coalesce(v_issue.breakdown_mode, 'automatic'),
    'breakdown_instructions', v_issue.breakdown_instructions,
    'guidelines', public.assemble_project_guidelines(v_issue.project_id),
    'learnings', public.assemble_project_learnings(v_issue.project_id)
  );

  perform public.seed_issue_instructions(p_issue, 'breakdown');

  insert into public.runs (org_id, issue_id, provider, status, kind, input_context, prev_issue_status)
  values (v_issue.org_id, p_issue, 'claude', 'queued', 'breakdown', v_context, v_issue.status)
  returning id into v_run;

  insert into public.issue_events (org_id, issue_id, type, payload)
  values (v_issue.org_id, p_issue, 'breakdown-dispatched',
          jsonb_build_object('run_id', v_run));

  return v_run;
end;
$$;
