-- 053_instruction_set: work item instruction set — a living work plan
-- attached to the ISSUE, not the run (US-5.11).
--
-- Seeded at dispatch (both dispatchers) when the item has none yet, from
-- the run kind's expectations (the US-5.14 worker_instructions templates)
-- plus the item's story/acceptance criteria and approved plan when
-- present. Manager-editable at any stage; runs read it live, so nothing
-- is lost across retries or worker hand-offs. The frozen input_context
-- snapshot stays untouched — the instruction set is its living counterpart.

alter table public.issues add column instruction_set text;

-- Seed once: a no-op when the item already carries instructions (manager
-- edits are never overwritten by the factory).
create or replace function public.seed_issue_instructions(p_issue uuid, p_kind text)
returns void
language plpgsql
as $$
declare
  v_issue public.issues%rowtype;
  v_text text;
  v_ac text;
  v_plan text;
begin
  select * into v_issue from public.issues where id = p_issue;
  if not found
     or (v_issue.instruction_set is not null
         and length(trim(v_issue.instruction_set)) > 0) then
    return;
  end if;

  v_text := '## Expectations — ' || p_kind || ' run' || E'\n\n'
    || coalesce(public.worker_instruction_for(v_issue.project_id, p_kind), '');

  if v_issue.body is not null and length(trim(v_issue.body)) > 0 then
    v_text := v_text || E'\n\n## Story\n\n' || v_issue.body;
  end if;

  select string_agg('- ' || ac.value, E'\n')
  into v_ac
  from jsonb_array_elements_text(
    coalesce(v_issue.acceptance_criteria, '[]'::jsonb)
  ) ac;
  if v_ac is not null then
    v_text := v_text || E'\n\n## Acceptance criteria\n\n' || v_ac;
  end if;

  select a.content into v_plan
  from public.artifacts a
  where a.issue_id = p_issue and a.kind = 'plan' and a.status = 'approved'
  order by a.version desc limit 1;
  if v_plan is not null then
    v_text := v_text || E'\n\n## Approved plan\n\n' || v_plan;
  end if;

  update public.issues set instruction_set = v_text where id = p_issue;

  insert into public.issue_events (org_id, issue_id, type, payload)
  values (v_issue.org_id, p_issue, 'instructions-seeded',
          jsonb_build_object('kind', p_kind));
end;
$$;

-- dispatch_issue v5: seed the instruction set before queueing the run.
-- Everything else is unchanged from the 037 revision.
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
  v_approved_prd_id uuid;
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

  if v_kind = 'code' and not v_has_approved_plan then
    raise exception 'code run requires an approved plan';
  end if;

  if v_issue.type = 'feature' and v_kind = 'plan' then
    select id into v_approved_prd_id from public.artifacts
    where issue_id = p_issue and kind = 'prd' and status = 'approved'
    order by version desc limit 1;
    if v_approved_prd_id is null then
      raise exception 'feature requires an approved PRD before planning';
    end if;
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

  insert into public.runs (org_id, issue_id, provider, status, kind, input_context)
  values (v_issue.org_id, p_issue, 'claude', 'queued', v_kind, v_context)
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

-- dispatch_prd_draft v2: same seeding guarantee for prd runs.
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

  insert into public.runs (org_id, issue_id, provider, status, kind, input_context)
  values (v_issue.org_id, p_issue, 'claude', 'queued', 'prd', v_context)
  returning id into v_run;

  insert into public.issue_events (org_id, issue_id, type, payload)
  values (v_issue.org_id, p_issue, 'prd-dispatched', jsonb_build_object('run_id', v_run));

  return v_run;
end;
$$;
