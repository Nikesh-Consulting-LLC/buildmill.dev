-- 189_preview_instructions: what the agent will read, before it reads it (US-49.1).
--
-- `issues.instruction_set` is seeded ONCE, inside the dispatch RPCs, and read
-- live by workers thereafter (migration 053). So the text that steers every
-- future run on an item is decided at a moment the manager cannot see, and by
-- the time it is visible on a tab it has already been sent.
--
-- Two extractions make it previewable without a second implementation that
-- would drift from the dispatcher within a phase:
--
--   build_issue_instructions(issue, kind)  the ASSEMBLY, lifted verbatim out
--     of 053's seeder, which now delegates to it and keeps its contract:
--     same signature, same skip-if-present guard, same instructions-seeded
--     event, so every dispatcher calls exactly what it called before.
--
--   dispatch_kind_for(issue, kind)  the plan-vs-code RESOLUTION, lifted
--     verbatim out of migration 166's dispatch_issue, which now delegates to
--     it. A preview headed "plan run" over text seeded for a code run is
--     worse than no preview, and that is what a client-side guess produces.
--
-- preview_issue_instructions is the browser's read-only view over both. It is
-- security definer because worker_instructions is default-deny to the client
-- (migration 052/057) — so it gates on is_org_member itself.

-- ------------------------------------------------------------- the assembly
create or replace function public.build_issue_instructions(p_issue uuid, p_kind text)
returns text
language plpgsql
stable
as $$
declare
  v_issue public.issues%rowtype;
  v_text text;
  v_ac text;
  v_plan text;
begin
  select * into v_issue from public.issues where id = p_issue;
  if not found then
    return null;
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

  return v_text;
end;
$$;

-- seed_issue_instructions v2: the guard and the audit, assembly delegated.
create or replace function public.seed_issue_instructions(p_issue uuid, p_kind text)
returns void
language plpgsql
as $$
declare
  v_org uuid;
  v_existing text;
  v_text text;
begin
  select org_id, instruction_set into v_org, v_existing
  from public.issues where id = p_issue;
  if not found
     or (v_existing is not null and length(trim(v_existing)) > 0) then
    return;
  end if;

  v_text := public.build_issue_instructions(p_issue, p_kind);
  if v_text is null then
    return;
  end if;

  update public.issues set instruction_set = v_text where id = p_issue;

  insert into public.issue_events (org_id, issue_id, type, payload)
  values (v_org, p_issue, 'instructions-seeded',
          jsonb_build_object('kind', p_kind));
end;
$$;

-- ------------------------------------------------------- the kind resolution
-- Migration 166's block, unchanged in substance: the inference when no kind is
-- named, the legality test when one is, and the two guards that follow it.
create or replace function public.dispatch_kind_for(p_issue uuid, p_kind text default null)
returns text
language plpgsql
stable
as $$
declare
  v_status text;
  v_type text;
  v_has_approved_plan boolean;
  v_can_plan boolean;
  v_can_code boolean;
  v_kind text;
begin
  select status, type into v_status, v_type
  from public.issues where id = p_issue;
  if not found then
    raise exception 'issue not found';
  end if;

  select exists(
    select 1 from public.artifacts
    where issue_id = p_issue and kind = 'plan' and status = 'approved'
  ) into v_has_approved_plan;

  v_can_plan := v_status in ('draft', 'ready', 'failed', 'needs-fixes', 'planned');
  v_can_code := v_has_approved_plan
                and v_status in ('planned', 'needs-fixes', 'failed');

  if p_kind is null then
    if v_has_approved_plan and v_status in ('planned', 'needs-fixes') then
      v_kind := 'code';
    elsif v_status in ('draft', 'ready', 'failed') then
      v_kind := 'plan';
    elsif v_status = 'needs-fixes' and not v_has_approved_plan then
      v_kind := 'plan';
    else
      raise exception 'issue is not dispatchable from status "%"', v_status;
    end if;
  elsif p_kind = 'plan' then
    if not v_can_plan then
      raise exception 'issue is not dispatchable for planning from status "%"', v_status;
    end if;
    v_kind := 'plan';
  elsif p_kind = 'code' then
    if not v_has_approved_plan then
      raise exception 'code run requires an approved plan';
    end if;
    if not v_can_code then
      raise exception 'issue is not dispatchable for coding from status "%"', v_status;
    end if;
    v_kind := 'code';
  else
    raise exception 'unknown run kind "%" — expected "plan" or "code"', p_kind;
  end if;

  -- US-11.2: a feature is not planned directly, however the kind was chosen.
  if v_type = 'feature' and v_kind = 'plan' then
    raise exception 'a feature is not planned directly — approve its PRD and break it into stories, then plan those';
  end if;

  if v_kind = 'code' and not v_has_approved_plan then
    raise exception 'code run requires an approved plan';
  end if;

  return v_kind;
end;
$$;

-- dispatch_issue: the live body, with the resolution block replaced by the
-- call. Everything else — every guard, the context, the seeding, the event —
-- is byte for byte what was running before this migration.
create or replace function public.dispatch_issue(p_issue uuid, p_kind text default null)
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

  v_kind := public.dispatch_kind_for(p_issue, p_kind);

  select * into v_project from public.projects where id = v_issue.project_id;

  if v_kind = 'code'
     and coalesce(v_project.build_mode, 'story') in ('feature', 'epic')
     and v_issue.parent_id is not null
     and v_issue.status not in ('failed', 'needs-fixes')
  then
    declare
      v_parent_label text;
      v_sibling_count int;
    begin
      select coalesce(
               case when e.number is not null and p.item_no is not null
                 then 'FEAT-' || e.number || '.' || p.item_no
               end,
               p.title)
        into v_parent_label
      from public.issues p
      left join public.epics e on e.id = p.epic_id
      where p.id = v_issue.parent_id;

      select count(*) into v_sibling_count
      from public.issues c
      where c.parent_id = v_issue.parent_id and c.abandoned_at is null;

      raise exception
        '% owns the build — dispatch the feature to build all % stories',
        coalesce(v_parent_label, 'the feature'), v_sibling_count;
    end;
  end if;

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
  else
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

  perform public.seed_issue_instructions(p_issue, v_kind);

  v_pre_status := v_issue.status;

  insert into public.runs (org_id, issue_id, provider, status, kind, input_context, prev_issue_status)
  values (v_issue.org_id, p_issue, 'claude', 'queued', v_kind, v_context, v_pre_status)
  returning id into v_run;

  update public.issues set status = 'queued' where id = p_issue;

  insert into public.issue_events (org_id, issue_id, type, payload)
  values (
    v_issue.org_id,
    p_issue,
    case when v_kind = 'plan' then 'plan-dispatched' else 'dispatched' end,
    jsonb_build_object(
      'run_id', v_run,
      'kind', v_kind,
      'from_status', v_pre_status,
      'kind_chosen_by', case when p_kind is null then 'inferred' else 'manager' end
    )
  );

  return v_run;
end;
$$;

-- ------------------------------------------------------------- the preview
-- `p_kind` null means "whatever a dispatch would choose" — the rail's own
-- button. 'plan'/'code' are validated by the same predicates the dispatcher
-- applies, so a preview that would be refused says so instead of showing text
-- for a run that cannot happen. Every other kind (prd, breakdown, elaborate,
-- wireframe) is taken verbatim: those dispatchers hard-code theirs.
create or replace function public.preview_issue_instructions(p_issue uuid, p_kind text default null)
returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare
  v_org uuid;
  v_existing text;
  v_kind text;
begin
  select org_id, instruction_set into v_org, v_existing
  from public.issues where id = p_issue;
  if not found then
    raise exception 'issue not found';
  end if;
  if not public.is_org_member(v_org) then
    raise exception 'not a member of this org';
  end if;

  if p_kind is null or p_kind in ('plan', 'code') then
    v_kind := public.dispatch_kind_for(p_issue, p_kind);
  else
    v_kind := p_kind;
  end if;

  -- A non-empty set is what the run will read, verbatim: the seeder skips an
  -- item that already carries one, so nothing is "about to be written" here.
  if v_existing is not null and length(trim(v_existing)) > 0 then
    return jsonb_build_object(
      'kind', v_kind,
      'seeded', false,
      'instruction_set', v_existing
    );
  end if;

  return jsonb_build_object(
    'kind', v_kind,
    'seeded', true,
    'instruction_set', public.build_issue_instructions(p_issue, v_kind)
  );
end;
$$;

revoke all on function public.preview_issue_instructions(uuid, text) from public;
grant execute on function public.preview_issue_instructions(uuid, text) to authenticated, service_role;
