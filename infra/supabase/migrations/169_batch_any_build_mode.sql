-- 169: US-41.1 — a feature's stories move to the next phase together, in order.
--
-- Two changes to batch dispatch, asked for on 2026-07-28 ("this will save me
-- clicking again and again" / "dispatched in the order of the story number").
--
-- 1. THE BUILD-MODE REFUSAL GOES.
--
--    `dispatch_feature_batch` opened with:
--
--      if v_mode not in ('feature', 'epic') then
--        raise exception 'batch dispatch needs build mode feature or epic ...';
--
--    That coupled a convenience to a governance setting deciding something
--    else entirely: whether the FEATURE owns the code build (us-22.10).
--    Wanting to dispatch six stories at once is not the same as wanting one
--    pull request for all six. On a `story`-mode project — the default —
--    there was no bulk dispatch at all, which is the clicking being
--    complained about.
--
--    What build mode still decides is unchanged, and is now the ONLY thing it
--    decides here:
--      feature/epic → one feature-owned code run carrying N run_items
--      story        → N independent code runs, one per story, one PR each
--    Same button, same ordering, different unit of work.
--
-- 2. THE ORDERING BECOMES TOTAL.
--
--    Both loops ordered `by sub_no nulls last, created_at`, which looks safe
--    and is not. A breakdown inserts every sibling in ONE statement, so
--    `created_at` is identical to the microsecond across all of them:
--
--      Persistence layer …     sub_no 1   2026-07-25 21:47:21.763457+00
--      Registration endpoint … sub_no 2   2026-07-25 21:47:21.763457+00
--      …                       …          (identical, all six)
--
--    It is a constant, not a tiebreaker. Any story with a null `sub_no`
--    therefore sorts arbitrarily, and two of them sort arbitrarily against
--    each other — Postgres may return equal sort keys in any order and
--    nothing makes that stable between calls. The queue drains serially, so
--    dispatch order IS execution order: "persistence layer last" is a failed
--    build, not a cosmetic complaint.
--
--    `order by sub_no nulls last, item_no nulls last, id` — `id` last so
--    equal keys cannot reorder between calls. `created_at` is dropped: it
--    implies a precision it does not have. Every loop uses the same clause.

create or replace function public.feature_dispatch_phase(p_feature uuid)
returns jsonb
language sql
stable
as $$
  with kids as (
    select c.id, c.status,
           exists (
             select 1 from public.artifacts a
             where a.issue_id = c.id and a.kind = 'plan' and a.status = 'approved'
           ) as has_plan
    from public.issues c
    where c.parent_id = p_feature and c.abandoned_at is null
  ),
  -- US-41.1: the confirm has to say whether a code batch is ONE run over
  -- every story or one run each, and that is the build mode's answer. It
  -- comes from here so the label and the dispatcher read the same source —
  -- the button guessing is how a confirm drifts from what it does.
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
           -- US-41.1: the bulk action is offered only when every story agrees
           -- on where it is. A feature with three `planned` and three `draft`
           -- gets none, because "next" would mean two different things.
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
    when buildable > 0 and unplanned > 0 then jsonb_build_object(
      'phase', 'blocked',
      'reason', format('%s of %s stories still need an approved plan before the feature can build',
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
       -- Named only when they agree; a common stage that isn't common is
       -- worse than none, because the button would label itself with it.
       'common_stage', case when stages = 1 then lowest_status end,
       'build_mode', (select build_mode from feat))
  from agg
$$;

comment on function public.feature_dispatch_phase(uuid) is
  'US-27.11 / US-41.1: what a batch dispatch would do, from the same '
  'predicates dispatch_feature_batch uses, plus whether every story sits at '
  'one stage (same_stage/common_stage) so the bulk action can name itself.';


create or replace function public.dispatch_feature_batch(p_feature uuid)
returns jsonb
language plpgsql
as $$
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

  select * into v_project from public.projects where id = v_feature.project_id;
  v_mode := coalesce(v_project.build_mode, 'story');
  -- US-41.1: no build-mode refusal. The mode chooses the SHAPE of the code
  -- phase below; it no longer decides whether batching is allowed at all.

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

  -- ---------------------------------------------------------------- plan
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
    raise exception
      'not ready to build: % stor% in this feature still need% an approved plan',
      v_unplanned,
      case when v_unplanned = 1 then 'y' else 'ies' end,
      case when v_unplanned = 1 then 's' else '' end;
  end if;

  -- ------------------------------------------------- code, in story mode
  -- US-41.1: one run per story. `dispatch_issue` applies every guard it
  -- normally would — this is the same dispatch the manager would perform by
  -- hand six times, in a defined order, in one call.
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

  -- --------------------------------------------- code, in feature/epic mode
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
$$;

comment on function public.dispatch_feature_batch(uuid) is
  'US-20.5 / US-41.1: dispatch every eligible story in a feature, in '
  'sub_no order with a total tiebreaker. Works in every build mode: '
  'feature/epic produce one feature-owned run carrying run_items, story mode '
  'produces one run per story.';

notify pgrst, 'reload schema';
