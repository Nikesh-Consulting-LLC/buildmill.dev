-- 166: let the caller name the phase a single dispatch runs.
--
-- `dispatch_issue` has always INFERRED plan-vs-code from the issue's status
-- and whether it holds an approved plan. That is the right default and it
-- stays the default — passing no kind behaves exactly as it did.
--
-- What it could not do is take an instruction. A manager looking at a
-- feature's six stories cannot say "re-plan this one" (a story at `planned`
-- infers `code`) or "build this one" (a story left `failed` by a build infers
-- `plan` — the same hole migration 146 closed for the BATCH path but not for
-- this one). The Stories list on a feature now offers those two as explicit
-- per-story actions, so the RPC has to be able to hear them.
--
-- The legality rules are unchanged in substance; they are only made
-- addressable:
--
--   plan  — draft, ready, failed, needs-fixes, planned
--           (`planned` is the re-plan case: the approved plan stands until a
--           new one is approved over it, exactly as a send-back re-plan does)
--   code  — planned, needs-fixes, failed, AND an approved plan
--           (`failed` per migration 146: the approved-plan test decides the
--           phase, not the status)
--
-- Every other guard — abandoned, feature-with-children, a feature is never
-- planned directly, the feature owns the build in feature/epic mode — applies
-- to a named kind exactly as it applies to an inferred one. Naming a phase
-- chooses between the legal ones; it does not buy past any of them.
--
-- The one-argument function is DROPPED rather than left beside the new one:
-- two overloads would make every existing `dispatch_issue(x)` call site
-- ambiguous. Callers inside the database (126's auto-approve, 139/146's batch)
-- keep working untouched — they resolve to the new signature's default.

drop function if exists public.dispatch_issue(uuid);

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
  v_has_approved_plan boolean;
  v_can_plan boolean;
  v_can_code boolean;
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

  v_can_plan := v_issue.status in ('draft', 'ready', 'failed', 'needs-fixes', 'planned');
  v_can_code := v_has_approved_plan
                and v_issue.status in ('planned', 'needs-fixes', 'failed');

  if p_kind is null then
    -- The inference, byte for byte as it was.
    if v_has_approved_plan and v_issue.status in ('planned', 'needs-fixes') then
      v_kind := 'code';
    elsif v_issue.status in ('draft', 'ready', 'failed') then
      v_kind := 'plan';
    elsif v_issue.status = 'needs-fixes' and not v_has_approved_plan then
      v_kind := 'plan';
    else
      raise exception 'issue is not dispatchable from status "%"', v_issue.status;
    end if;
  elsif p_kind = 'plan' then
    if not v_can_plan then
      raise exception 'issue is not dispatchable for planning from status "%"', v_issue.status;
    end if;
    v_kind := 'plan';
  elsif p_kind = 'code' then
    if not v_has_approved_plan then
      raise exception 'code run requires an approved plan';
    end if;
    if not v_can_code then
      raise exception 'issue is not dispatchable for coding from status "%"', v_issue.status;
    end if;
    v_kind := 'code';
  else
    raise exception 'unknown run kind "%" — expected "plan" or "code"', p_kind;
  end if;

  -- US-11.2: a feature is not planned directly, however the kind was chosen.
  if v_issue.type = 'feature' and v_kind = 'plan' then
    raise exception 'a feature is not planned directly — approve its PRD and break it into stories, then plan those';
  end if;

  if v_kind = 'code' and not v_has_approved_plan then
    raise exception 'code run requires an approved plan';
  end if;

  select * into v_project from public.projects where id = v_issue.project_id;

  -- US-22.10: in feature/epic mode the FEATURE owns the code build. Refuse
  -- here so the API and the greyed button agree — a button that declines is
  -- a better answer than a run that never moves.
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
    -- A re-plan reads the plan it is replacing, whether the manager asked for
    -- it outright or a send-back did. Without this the agent rewrites from
    -- scratch and quietly loses whatever was already right.
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
      -- Whether a person named this phase or the factory inferred it. The
      -- 2026-07-26 six-silent-replans incident was unreadable afterwards
      -- precisely because the event could not say which.
      'kind_chosen_by', case when p_kind is null then 'inferred' else 'manager' end
    )
  );

  return v_run;
end;
$$;

-- The one-arg function was revoked from PUBLIC and never granted to `anon`; a
-- freshly created function inherits both, so restore the exact ACL rather than
-- taking the default. (Nothing here is security definer, so an anon caller
-- would fail on RLS anyway — but "dispatch is for signed-in members" should be
-- readable from the grants, not deduced.)
revoke execute on function public.dispatch_issue(uuid, text) from public;
revoke execute on function public.dispatch_issue(uuid, text) from anon;
grant execute on function public.dispatch_issue(uuid, text) to authenticated, service_role;

notify pgrst, 'reload schema';
