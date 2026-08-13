-- 235_issue_dispatch_blocks (US-74.5): let a surface ask "can this work item
-- be dispatched right now, and if not, why?" -- and get the answer from the
-- SAME code the factory enforces, never a re-derivation.
--
-- The Things-to-Do hub lists items awaiting the manager. Some of them cannot
-- actually move: dispatch_issue would refuse them outright, or the run they
-- create would be parked by run_hold_reason the moment it hit the pool. The
-- hub had no way to know either, so it offered a button that errored.
--
-- Three moves, no rule changes:
--
--   1. run_hold_reason's body moves verbatim into issue_hold_reason(issue,
--      kind) and run_hold_reason becomes a thin wrapper over it. The body
--      only ever read three things off the run row -- issue_id, kind and
--      project_id -- and the last is the issue's own project, so the split is
--      mechanical. Now the question can be asked BEFORE a run exists.
--      (One text change: the earlier-feature hold now names the feature it is
--      waiting on. The phrase tests match on is untouched.)
--
--   2. dispatch_issue's two refusals move into issue_dispatch_refusal(issue,
--      kind), which returns the message instead of raising; dispatch_issue
--      calls it and raises what it returns. The wording is byte-identical, so
--      what the manager reads before clicking is what the RPC would have said.
--
--   3. issue_dispatch_block + org_issue_dispatch_blocks join the two for the
--      UI: a hard refusal (the button would error) or a soft wait (the run
--      would be created and immediately parked).
--
-- Behavior is deliberately unchanged: dispatch_issue still refuses exactly
-- what it refused, and the pool still holds exactly what it held.

-- 1 ------------------------------------------------------------------------
-- The hold rules, addressed by (issue, kind) instead of by run. Carried
-- forward verbatim from the live body (migration 172 + the 176/185
-- exemptions), with v_run.kind -> p_kind, v_run.issue_id -> p_issue and
-- v_run.project_id -> the issue's project.

create or replace function public.issue_hold_reason(p_issue uuid, p_kind text)
returns text
language plpgsql
stable
as $function$
declare
  v_issue public.issues%rowtype;
  v_mode text;
  v_feature uuid;
  v_epic uuid;
  v_feat_no int;
  v_epic_no int;
  v_cnt int;
  v_blocker text;
begin
  if p_issue is null then
    return null;
  end if;
  select * into v_issue from public.issues where id = p_issue;
  if not found then
    return null;
  end if;

  -- US-43.5: a guidelines refresh is not delivery work. It orders after
  -- nothing and nothing orders after it, so no rule below should reach it.
  -- Every rule below already misses it by accident; stated here it survives
  -- the next rebuild.
  if p_kind = 'guidelines' then
    return null;
  end if;

  -- US-15.3 (always on): a story run is held while any non-abandoned sibling
  -- is still draft (the split isn't curated yet).
  -- US-44.1: an elaborate run is exempt from THIS rule only. It exists to
  -- fix the very condition the rule holds on (a fresh breakdown set where
  -- every sibling is still draft), so holding it here is a deadlock by
  -- construction. It stays subject to every ordering rule below.
  if v_issue.parent_id is not null and p_kind not in ('elaborate', 'wireframe') then
    select count(*) into v_cnt
    from public.issues sib
    where sib.parent_id = v_issue.parent_id
      and sib.abandoned_at is null
      and sib.status = 'draft';
    if v_cnt > 0 then
      return format('waiting: %s sibling stor%s still being curated',
        v_cnt, case when v_cnt = 1 then 'y' else 'ies' end);
    end if;
  end if;

  select build_mode into v_mode from public.projects where id = v_issue.project_id;

  if v_mode = 'feature' then
    if v_issue.type = 'feature' then
      v_feature := v_issue.id;
    elsif v_issue.parent_id is not null then
      v_feature := v_issue.parent_id;
    else
      v_feature := null;
    end if;

    if v_feature is not null then
      select epic_id, item_no into v_epic, v_feat_no
      from public.issues where id = v_feature;
      select number into v_epic_no from public.epics where id = v_epic;

      -- US-74.5: same predicate as before, but the blocking feature is now
      -- named -- "waiting" without saying on what is the complaint the hub
      -- exists to answer.
      select coalesce(
               case when fe.number is not null and f.item_no is not null
                 then 'FEAT-' || fe.number || '.' || f.item_no || ' · ' || f.title
               end,
               f.title)
        into v_blocker
      from public.issues f
      join public.epics fe on fe.id = f.epic_id
      where f.project_id = v_issue.project_id
        and f.type = 'feature'
        and f.abandoned_at is null
        and f.status <> 'done'
        and f.id <> v_feature
        and (fe.number, f.item_no) < (v_epic_no, v_feat_no)
      order by fe.number, f.item_no
      limit 1;
      if v_blocker is not null then
        return format('waiting on an earlier feature to finish — %s', v_blocker);
      end if;

      if p_kind = 'code' then
        select count(*) into v_cnt
        from public.issues sib
        where sib.parent_id = v_feature
          and sib.abandoned_at is null
          and not exists (
            select 1 from public.artifacts a
            where a.issue_id = sib.id
              and a.kind = 'plan' and a.status = 'approved'
          );
        if v_cnt > 0 then
          return format('waiting: %s sibling stor%s still need plan approval',
            v_cnt, case when v_cnt = 1 then 'y' else 'ies' end);
        end if;
      end if;
    end if;

  elsif v_mode = 'epic' then
    v_epic := v_issue.epic_id;
    -- No epic → the issue is its own singleton batch; epic-wide rules don't apply.
    if v_epic is not null then
      -- Document phase: a plan run waits until every feature in the epic is
      -- broken down (has at least one non-abandoned child story).
      if p_kind = 'plan' then
        select count(*) into v_cnt
        from public.issues f
        where f.epic_id = v_epic
          and f.type = 'feature'
          and f.abandoned_at is null
          and not exists (
            select 1 from public.issues c
            where c.parent_id = f.id and c.abandoned_at is null
          );
        if v_cnt > 0 then
          return format('waiting: %s feature%s in this epic aren''t broken down yet',
            v_cnt, case when v_cnt = 1 then '' else 's' end);
        end if;
      -- Plan phase: a code run waits until every non-abandoned story in the
      -- epic has an approved plan.
      elsif p_kind = 'code' then
        select count(*) into v_cnt
        from public.issues s
        where s.epic_id = v_epic
          and s.parent_id is not null
          and s.abandoned_at is null
          and not exists (
            select 1 from public.artifacts a
            where a.issue_id = s.id
              and a.kind = 'plan' and a.status = 'approved'
          );
        if v_cnt > 0 then
          return format('waiting: %s stor%s in this epic still need plan approval',
            v_cnt, case when v_cnt = 1 then 'y' else 'ies' end);
        end if;
      end if;
    end if;
  end if;

  -- US-20.5: the batch drains one story at a time, and stops when a story
  -- needs the manager. Both rules are scoped to a story inside a feature, in
  -- feature or epic mode (an epic is built feature by feature).
  if v_mode in ('feature', 'epic') and v_issue.parent_id is not null then

    -- (d) trouble pauses the feature — but NEVER the troubled story's own
    -- run, or the fix would be held by the thing it fixes.
    if not public.issue_in_trouble(v_issue.id) then
      select format('%s-%s.%s.%s',
               case sib.type when 'bug' then 'BUG'
                             when 'chore' then 'CHORE'
                             else 'US' end,
               ep.number, sib.item_no, sib.sub_no)
        into v_blocker
      from public.issues sib
      join public.epics ep on ep.id = sib.epic_id
      where sib.parent_id = v_issue.parent_id
        and sib.abandoned_at is null
        and sib.id <> v_issue.id
        and public.issue_in_trouble(sib.id)
      order by sib.sub_no nulls last
      limit 1;
      if v_blocker is not null then
        return format('paused: story %s needs your attention', v_blocker);
      end if;
    end if;

    -- (c) one story in flight per feature, in sub_no order. Holds on the RUN
    -- (queued/running), not on approval — story N+1 starts as soon as story N
    -- hands back, so the manager reviews while the next story works.
    if v_issue.sub_no is not null then
      select format('%s-%s.%s.%s',
               case sib.type when 'bug' then 'BUG'
                             when 'chore' then 'CHORE'
                             else 'US' end,
               ep.number, sib.item_no, sib.sub_no)
        into v_blocker
      from public.issues sib
      join public.epics ep on ep.id = sib.epic_id
      join public.runs r on r.issue_id = sib.id
      where sib.parent_id = v_issue.parent_id
        and sib.abandoned_at is null
        and sib.sub_no is not null
        and sib.sub_no < v_issue.sub_no
        and r.kind = p_kind
        and r.status in ('queued', 'running')
      order by sib.sub_no
      limit 1;
      if v_blocker is not null then
        return format('waiting: story %s ahead of this one is still running',
          v_blocker);
      end if;
    end if;
  end if;

  return null;
end;
$function$;

comment on function public.issue_hold_reason(uuid, text) is
  'Why a run of this kind for this work item would be held by the pool, or '
  'null if nothing holds it. The rules live here; run_hold_reason wraps this '
  'for an existing run and org_issue_dispatch_blocks asks it before one '
  'exists (US-74.5).';

-- run_hold_reason keeps its signature and its meaning; it is now a lookup
-- plus a delegation. The pool calls this, so the pool and the hub cannot
-- disagree about what is held.
create or replace function public.run_hold_reason(p_run uuid)
returns text
language plpgsql
stable
as $function$
declare
  v_run public.runs%rowtype;
begin
  select * into v_run from public.runs where id = p_run;
  if not found or v_run.issue_id is null then
    return null;
  end if;
  return public.issue_hold_reason(v_run.issue_id, v_run.kind);
end;
$function$;

-- 2 ------------------------------------------------------------------------
-- The dispatch-time refusals, as text. Same two guards dispatch_issue has
-- always raised, same wording, in one place both callers read.

create or replace function public.issue_dispatch_refusal(p_issue uuid, p_kind text)
returns text
language plpgsql
stable
as $function$
declare
  v_issue public.issues%rowtype;
  v_project public.projects%rowtype;
  v_parent_label text;
  v_sibling_count int;
  v_holder record;
begin
  select * into v_issue from public.issues where id = p_issue;
  if not found then
    return null;
  end if;
  select * into v_project from public.projects where id = v_issue.project_id;

  -- The feature owns the build of its stories.
  if p_kind = 'code'
     and coalesce(v_project.build_mode, 'story') in ('feature', 'epic')
     and v_issue.parent_id is not null
     and v_issue.status not in ('failed', 'needs-fixes')
  then
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

    return format('%s owns the build — dispatch the feature to build all %s stories',
      coalesce(v_parent_label, 'the feature'), v_sibling_count);
  end if;

  -- One build unit in flight per project.
  if coalesce(v_project.sequential_only, false) then
    select i.id, i.title,
           coalesce(
             case when e.number is not null and i.item_no is not null
               then 'US-' || e.number || '.' || i.item_no
             end,
             i.title
           ) as label
      into v_holder
    from public.issues i
    left join public.epics e on e.id = i.epic_id
    where i.project_id = v_issue.project_id
      and i.id <> v_issue.id
      and i.abandoned_at is null
      and i.status in ('planning', 'plan-review', 'planned', 'queued',
                        'running', 'needs-fixes', 'in-review', 'failed')
    order by i.updated_at asc
    limit 1;

    if found then
      return format('%s must reach merged before you can dispatch a new one (sequential build mode)',
        v_holder.label);
    end if;
  end if;

  return null;
end;
$function$;

comment on function public.issue_dispatch_refusal(uuid, text) is
  'The message dispatch_issue would raise for this work item and kind, or '
  'null if it would be accepted. dispatch_issue raises exactly this (US-74.5).';

-- dispatch_issue: the live body, with the two inline guards replaced by the
-- call above. Everything else is carried forward verbatim.
create or replace function public.dispatch_issue(p_issue uuid, p_kind text default null)
returns uuid
language plpgsql
as $function$
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
  v_refusal text;
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

  -- US-74.5: the feature-owns-the-build and sequential-only refusals now live
  -- in issue_dispatch_refusal so the UI can ask the same question without
  -- provoking the error. Same conditions, same wording.
  v_refusal := public.issue_dispatch_refusal(p_issue, v_kind);
  if v_refusal is not null then
    raise exception '%', v_refusal;
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
$function$;

-- 3 ------------------------------------------------------------------------
-- What the UI asks: one answer per work item, and whether it is a hard
-- refusal (the dispatch would error) or a soft wait (the run would be
-- created and immediately parked by the pool).

create or replace function public.issue_dispatch_block(p_issue uuid, p_kind text default null)
returns table (reason text, hard boolean)
language plpgsql
stable
as $function$
declare
  v_issue public.issues%rowtype;
  v_kind text;
  v_text text;
begin
  select * into v_issue from public.issues where id = p_issue;
  if not found or v_issue.abandoned_at is not null then
    return;
  end if;

  -- An issue that isn't dispatchable from its current status is not BLOCKED,
  -- it is simply not up for dispatch — the caller doesn't offer the action.
  begin
    v_kind := public.dispatch_kind_for(p_issue, p_kind);
  exception when others then
    return;
  end;

  v_text := public.issue_dispatch_refusal(p_issue, v_kind);
  if v_text is not null then
    reason := v_text;
    hard := true;
    return next;
    return;
  end if;

  v_text := public.issue_hold_reason(p_issue, v_kind);
  if v_text is not null then
    reason := v_text;
    hard := false;
    return next;
    return;
  end if;

  return;
end;
$function$;

comment on function public.issue_dispatch_block(uuid, text) is
  'Zero rows when this work item can be dispatched now. One row otherwise: '
  'hard = the dispatch would be refused, hard false = it would be accepted '
  'and the run parked by the pool (US-74.5).';

-- The org-wide sweep the Things-to-Do hub loads in one call, mirroring
-- org_queue_hold_reasons: security definer, gated on membership, and it
-- returns only the items that are actually blocked.
create or replace function public.org_issue_dispatch_blocks(p_org uuid)
returns table (issue_id uuid, reason text, hard boolean)
language sql
stable
security definer
set search_path to 'public'
as $function$
  select i.id, b.reason, b.hard
  from public.issues i
  cross join lateral public.issue_dispatch_block(i.id, null) b
  where i.org_id = p_org
    and i.abandoned_at is null
    and i.status in ('draft', 'ready', 'planned', 'needs-fixes', 'failed')
    and public.is_org_member(p_org)
$function$;

comment on function public.org_issue_dispatch_blocks(uuid) is
  'Every work item in the org that cannot be dispatched right now, with the '
  'reason and whether it is a refusal or a pool hold (US-74.5).';

-- Grants: match org_queue_hold_reasons exactly — signed-in callers only.
-- `revoke ... from public` alone is NOT enough: Supabase's default privileges
-- hand `anon` its own execute grant on every new function in this schema, and
-- a role-level grant survives a revoke aimed at PUBLIC. Both have to go, or a
-- logged-out caller keeps the privilege that was meant to be removed.
revoke all on function public.org_issue_dispatch_blocks(uuid) from public, anon;
revoke all on function public.issue_dispatch_block(uuid, text) from public, anon;
revoke all on function public.issue_dispatch_refusal(uuid, text) from public, anon;
revoke all on function public.issue_hold_reason(uuid, text) from public, anon;

grant execute on function public.org_issue_dispatch_blocks(uuid)
  to authenticated, service_role;
grant execute on function public.issue_dispatch_block(uuid, text)
  to authenticated, service_role;
grant execute on function public.issue_dispatch_refusal(uuid, text)
  to authenticated, service_role;
grant execute on function public.issue_hold_reason(uuid, text)
  to authenticated, service_role;
