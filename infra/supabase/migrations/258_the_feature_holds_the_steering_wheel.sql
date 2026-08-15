-- 258_the_feature_holds_the_steering_wheel (us-96.4): the feature owns the
-- initial plan of its stories, not just the build.
--
-- US-22.10 gave the feature the build; US-86.1 (migration 247) made it the
-- routing unit under switch 2 (route_feature_as_one) with one feature-owned
-- code run. Planning still routed story by story. From here, a story that
-- has NEVER been planned flows through the feature's batch; what stays
-- individually dispatchable is remediation and revision:
--
--   - failed / needs-fixes         (the trouble exemption, unchanged)
--   - any story with a plan artifact in ANY state — approved (re-plan),
--     superseded (the /replan endpoint's path), or draft (a sent-back plan)
--
-- Three coordinated changes, so no path wedges:
--
--   1. issue_dispatch_refusal: new plan-kind branch. The batch itself is
--      exempt through a transaction-local flag (factory.feature_batch) —
--      dispatch_feature_batch reuses dispatch_issue per child by design
--      (US-41.1), and it IS the feature-level dispatch.
--   2. dispatch_feature_batch: sets the flag, and learns the late-arrival
--      case — a story added after its siblings were planned used to hit
--      'not ready to build' while the new refusal would block planning it
--      individually. Now the batch plans exactly the unplanned children.
--   3. feature_dispatch_phase: the mixed case (buildable + unplanned) says
--      'plan', matching what the batch now does, so the button's label and
--      the dispatcher stay one source.

-- 1 ------------------------------------------------ the refusal, extended
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
begin
  select * into v_issue from public.issues where id = p_issue;
  if not found then
    return null;
  end if;
  select * into v_project from public.projects where id = v_issue.project_id;

  -- The feature owns the build of its stories (switch 2).
  if p_kind = 'code'
     and coalesce(v_project.route_feature_as_one, true)
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

  -- us-96.4: the feature owns the initial PLAN too. Scoped to stories that
  -- have never been planned — a plan artifact in any state means
  -- remediation or revision, which stays individual (the fix must never be
  -- held by the thing it fixes). The batch is exempt via the
  -- transaction-local flag it sets.
  if p_kind = 'plan'
     and coalesce(v_project.route_feature_as_one, true)
     and v_issue.parent_id is not null
     and v_issue.status in ('draft', 'ready')
     and coalesce(current_setting('factory.feature_batch', true), '') <> '1'
     and not exists (
       select 1 from public.artifacts a
       where a.issue_id = p_issue and a.kind = 'plan'
     )
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

    return format('%s owns the plan — dispatch the feature to plan all %s stories',
      coalesce(v_parent_label, 'the feature'), v_sibling_count);
  end if;

  return null;
end;
$function$;

comment on function public.issue_dispatch_refusal(uuid, text) is
  'The message dispatch_issue would raise for this work item and kind, or '
  'null if it would be accepted. US-86.1 deleted the sequential-only '
  'refusal. us-96.4: under switch 2 the feature owns both the build and '
  'the initial plan of its stories; remediation (failed/needs-fixes) and '
  'revision (any existing plan artifact) stay individually dispatchable, '
  'and dispatch_feature_batch is exempt via factory.feature_batch.';

-- 2 --------------------------------------- the batch: flag + late arrivals
-- Body carried from 169 (verified byte-identical on prod and dev) with two
-- changes marked us-96.4.
create or replace function public.dispatch_feature_batch(p_feature uuid)
returns jsonb
language plpgsql
as $function$
declare
  v_feature public.issues%rowtype;
  v_project public.projects%rowtype;
  v_mode text;
  v_child record;
  v_run uuid;
  v_kind text;
  v_dispatched jsonb := '[]'::jsonb;
  v_skipped jsonb := '[]'::jsonb;
  v_stories jsonb := '[]'::jsonb;
  v_context jsonb;
  v_position int := 0;
  v_code_phase boolean;
  v_feedback text;
  v_unplanned int;
  v_children int;
  v_phase_reason text;
begin
  select * into v_feature from public.issues where id = p_feature for update;
  if not found then
    raise exception 'feature not found';
  end if;
  if v_feature.type <> 'feature' then
    raise exception 'batch dispatch applies to a feature, not a %', v_feature.type;
  end if;
  if v_feature.abandoned_at is not null then
    raise exception 'feature is abandoned';
  end if;

  -- us-96.4: this IS the feature-level dispatch — exempt the per-child
  -- dispatch_issue calls below from the feature-owns-the-plan refusal.
  -- Transaction-local; nothing outside this call ever sees it.
  perform set_config('factory.feature_batch', '1', true);

  select * into v_project from public.projects where id = v_feature.project_id;
  v_mode := coalesce(v_project.build_mode, 'story');

  select count(*) into v_children
  from public.issues c
  where c.parent_id = p_feature and c.abandoned_at is null;
  if v_children = 0 then
    raise exception 'feature has no stories to dispatch';
  end if;

  select count(*) into v_unplanned
  from public.issues c
  where c.parent_id = p_feature
    and c.abandoned_at is null
    and not exists (
      select 1 from public.artifacts a
      where a.issue_id = c.id and a.kind = 'plan' and a.status = 'approved'
    );

  select exists (
    select 1
    from public.issues c
    where c.parent_id = p_feature
      and c.abandoned_at is null
      and c.status in ('planned', 'needs-fixes', 'failed')
      and exists (
        select 1 from public.artifacts a
        where a.issue_id = c.id and a.kind = 'plan' and a.status = 'approved'
      )
  ) into v_code_phase;

  if not v_code_phase then
    if v_unplanned = 0 then
      raise exception
        'refusing to plan: all % stories in this feature already hold an '
        'approved plan, so this would re-plan work that is ready to build. '
        'Their statuses (%) are not ones the build phase recognises — say '
        'what you want to happen to them first.',
        v_children,
        (select string_agg(distinct c.status, ', ')
           from public.issues c
          where c.parent_id = p_feature and c.abandoned_at is null);
    end if;

    v_phase_reason := format(
      '%s of %s stories have no approved plan yet',
      v_unplanned, v_children);
    for v_child in
      select id from public.issues
      where parent_id = p_feature and abandoned_at is null
      order by sub_no nulls last, item_no nulls last, id
    loop
      begin
        v_run := public.dispatch_issue(v_child.id);
        select kind into v_kind from public.runs where id = v_run;
        v_dispatched := v_dispatched || jsonb_build_object(
          'issue_id', v_child.id, 'run_id', v_run, 'kind', v_kind);
      exception when others then
        v_skipped := v_skipped || jsonb_build_object(
          'issue_id', v_child.id, 'reason', SQLERRM);
      end;
    end loop;

    return jsonb_build_object(
      'dispatched', v_dispatched,
      'skipped', v_skipped,
      'phase', 'plan',
      'phase_reason', v_phase_reason,
      'story_count', jsonb_array_length(v_dispatched));
  end if;

  if v_unplanned > 0 then
    -- us-96.4: stories added after their siblings were planned are planned
    -- NOW, rather than wedging the feature between 'not ready to build'
    -- here and the individual-dispatch refusal outside.
    v_phase_reason := format(
      '%s late stor%s still need%s a plan before the feature can build',
      v_unplanned,
      case when v_unplanned = 1 then 'y' else 'ies' end,
      case when v_unplanned = 1 then 's' else '' end);
    for v_child in
      select c.id from public.issues c
      where c.parent_id = p_feature
        and c.abandoned_at is null
        and not exists (
          select 1 from public.artifacts a
          where a.issue_id = c.id and a.kind = 'plan' and a.status = 'approved'
        )
      order by c.sub_no nulls last, c.item_no nulls last, c.id
    loop
      begin
        v_run := public.dispatch_issue(v_child.id);
        select kind into v_kind from public.runs where id = v_run;
        v_dispatched := v_dispatched || jsonb_build_object(
          'issue_id', v_child.id, 'run_id', v_run, 'kind', v_kind);
      exception when others then
        v_skipped := v_skipped || jsonb_build_object(
          'issue_id', v_child.id, 'reason', SQLERRM);
      end;
    end loop;

    return jsonb_build_object(
      'dispatched', v_dispatched,
      'skipped', v_skipped,
      'phase', 'plan',
      'phase_reason', v_phase_reason,
      'story_count', jsonb_array_length(v_dispatched));
  end if;

  if v_mode not in ('feature', 'epic') then
    for v_child in
      select id from public.issues
      where parent_id = p_feature and abandoned_at is null
      order by sub_no nulls last, item_no nulls last, id
    loop
      begin
        v_run := public.dispatch_issue(v_child.id, 'code');
        v_position := v_position + 1;
        v_dispatched := v_dispatched || jsonb_build_object(
          'issue_id', v_child.id, 'run_id', v_run, 'kind', 'code');
      exception when others then
        v_skipped := v_skipped || jsonb_build_object(
          'issue_id', v_child.id, 'reason', SQLERRM);
      end;
    end loop;

    if v_position = 0 then
      raise exception 'no story in this feature is ready to build';
    end if;

    return jsonb_build_object(
      'dispatched', v_dispatched,
      'skipped', v_skipped,
      'phase', 'code',
      'phase_reason', format(
        '%s stor%s dispatched to build, one run each',
        v_position, case when v_position = 1 then 'y' else 'ies' end),
      'story_count', v_position);
  end if;

  for v_child in
    select c.*,
           exists (
             select 1 from public.artifacts a
             where a.issue_id = c.id and a.kind = 'plan' and a.status = 'approved'
           ) as has_plan,
           e.number as epic_number
    from public.issues c
    left join public.epics e on e.id = c.epic_id
    where c.parent_id = p_feature and c.abandoned_at is null
    order by c.sub_no nulls last, c.item_no nulls last, c.id
  loop
    if not v_child.has_plan then
      v_skipped := v_skipped || jsonb_build_object(
        'issue_id', v_child.id, 'reason', 'no approved implementation plan');
      continue;
    end if;
    if v_child.status not in ('planned', 'needs-fixes', 'failed') then
      v_skipped := v_skipped || jsonb_build_object(
        'issue_id', v_child.id,
        'reason', format('not buildable from status "%s"', v_child.status));
      continue;
    end if;

    v_position := v_position + 1;

    select a.comment into v_feedback
    from public.approvals a
    where a.issue_id = v_child.id
      and a.gate = 'code-review'
      and a.decision = 'rejected'
    order by a.created_at desc
    limit 1;

    v_stories := v_stories || jsonb_build_object(
      'issue_id', v_child.id,
      'display_id', coalesce(
        case when v_child.epic_number is not null and v_child.item_no is not null
          then 'US-' || v_child.epic_number || '.' || v_child.item_no ||
               coalesce('.' || v_child.sub_no, '')
        end,
        left(v_child.id::text, 8)),
      'position', v_position,
      'prev_status', v_child.status,
      'title', v_child.title,
      'story', v_child.body,
      'acceptance_criteria', v_child.acceptance_criteria,
      'feedback', v_feedback,
      'test_cases', coalesce((
        select jsonb_agg(jsonb_build_object(
          'id', t.id, 'title', t.title, 'steps', t.steps,
          'expected_result', t.expected_result
        ) order by t.created_at)
        from public.test_cases t
        where t.issue_id = v_child.id and t.status = 'active'
      ), '[]'::jsonb)
    );
  end loop;

  if v_position = 0 then
    raise exception 'no story in this feature is ready to build';
  end if;

  v_phase_reason := format(
    '%s stor%s hold an approved plan and are ready to build',
    v_position, case when v_position = 1 then 'y' else 'ies' end);

  v_context := jsonb_build_object(
    'title', v_feature.title,
    'type', 'feature',
    'run_kind', 'code',
    'multi_story', true,
    'stories', v_stories,
    'repo_full_name', v_project.repo_full_name,
    'default_branch', v_project.default_branch,
    'guidelines', public.assemble_project_guidelines(v_feature.project_id),
    'learnings', public.assemble_project_learnings(v_feature.project_id),
    'prd', (
      select a.content from public.artifacts a
      where a.issue_id = p_feature and a.kind = 'prd' and a.status = 'approved'
      order by a.version desc limit 1
    )
  );

  perform public.seed_issue_instructions(p_feature, 'code');

  insert into public.runs
    (org_id, issue_id, project_id, provider, status, kind, input_context, prev_issue_status)
  values
    (v_feature.org_id, p_feature, v_feature.project_id, 'claude', 'queued', 'code',
     v_context, v_feature.status)
  returning id into v_run;

  insert into public.run_items (run_id, issue_id, org_id, position, prev_issue_status)
  select v_run,
         (s->>'issue_id')::uuid,
         v_feature.org_id,
         (s->>'position')::int,
         s->>'prev_status'
  from jsonb_array_elements(v_stories) s;

  update public.issues
  set status = 'queued'
  where id in (select (s->>'issue_id')::uuid from jsonb_array_elements(v_stories) s);

  insert into public.issue_events (org_id, issue_id, type, payload)
  select v_feature.org_id, (s->>'issue_id')::uuid, 'dispatched',
         jsonb_build_object('run_id', v_run, 'kind', 'code',
                            'feature_id', p_feature, 'batched', true)
  from jsonb_array_elements(v_stories) s;

  v_dispatched := jsonb_build_object(
    'issue_id', p_feature, 'run_id', v_run, 'kind', 'code',
    'stories', v_position);

  return jsonb_build_object(
    'dispatched', jsonb_build_array(v_dispatched),
    'skipped', v_skipped,
    'run_id', v_run,
    'phase', 'code',
    'phase_reason', v_phase_reason,
    'story_count', v_position);
end;
$function$;

-- 3 -------------------------------------- the phase label matches the act
create or replace function public.feature_dispatch_phase(p_feature uuid)
returns jsonb
language sql
stable
as $function$
  with kids as (
    select c.id, c.status,
           exists (
             select 1 from public.artifacts a
             where a.issue_id = c.id and a.kind = 'plan' and a.status = 'approved'
           ) as has_plan
    from public.issues c
    where c.parent_id = p_feature and c.abandoned_at is null
  ),
  feat as (
    select coalesce(p.build_mode, 'story') as build_mode
    from public.issues i
    join public.projects p on p.id = i.project_id
    where i.id = p_feature
  ),
  agg as (
    select count(*)::int as children,
           count(*) filter (where not has_plan)::int as unplanned,
           count(*) filter (
             where has_plan and status in ('planned', 'needs-fixes', 'failed')
           )::int as buildable,
           count(distinct status)::int as stages,
           min(status) as lowest_status
    from kids
  )
  select (case
    when children = 0 then jsonb_build_object(
      'phase', 'none', 'reason', 'this feature has no stories',
      'children', 0, 'buildable', 0, 'unplanned', 0)
    when buildable > 0 and unplanned = 0 then jsonb_build_object(
      'phase', 'code',
      'reason', format('%s of %s stories hold an approved plan and are ready to build',
                       buildable, children),
      'children', children, 'buildable', buildable, 'unplanned', unplanned)
    -- us-96.4: the mixed case dispatches PLANS for the late stories (see
    -- dispatch_feature_batch), so the button says what will happen instead
    -- of calling itself blocked.
    when buildable > 0 and unplanned > 0 then jsonb_build_object(
      'phase', 'plan',
      'reason', format('%s of %s stories still need a plan — dispatching plans them before the build',
                       unplanned, children),
      'children', children, 'buildable', buildable, 'unplanned', unplanned)
    when unplanned = 0 then jsonb_build_object(
      'phase', 'blocked',
      'reason', 'every story holds an approved plan but none is in a buildable status',
      'children', children, 'buildable', buildable, 'unplanned', unplanned)
    else jsonb_build_object(
      'phase', 'plan',
      'reason', format('%s of %s stories have no approved plan yet', unplanned, children),
      'children', children, 'buildable', buildable, 'unplanned', unplanned)
  end)
  || jsonb_build_object(
       'same_stage', stages = 1,
       'common_stage', case when stages = 1 then lowest_status end,
       'build_mode', (select build_mode from feat))
  from agg
$function$;

comment on function public.feature_dispatch_phase(uuid) is
  'US-27.11 / US-41.1 / us-96.4: what a batch dispatch would do, from the '
  'same predicates dispatch_feature_batch uses. The mixed buildable+'
  'unplanned case now answers ''plan'' — the batch plans late arrivals.';
