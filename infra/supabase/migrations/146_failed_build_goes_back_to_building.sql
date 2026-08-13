-- 146: a failed build goes back to building, not planning (US-27.11).
--
-- dispatch_feature_batch infers the phase from the children's rolled-up state
-- and recognised the code phase only for children in `planned` or
-- `needs-fixes`. When a feature's code run fails its stories are left
-- `failed`, which matches neither — so re-dispatching a feature whose build
-- just failed SILENTLY RE-PLANNED it.
--
-- That happened on 2026-07-26: the manager asked for six stories to go back to
-- coding and the factory queued six PLAN runs against stories that each
-- already held an approved plan and an approved test plan. The only signal was
-- six `plan-dispatched` events nobody was watching for. Getting back to the
-- code phase needed every story hand-edited to `needs-fixes` first.
--
-- Three changes, in order of how much they matter:
--
--   1. `failed` joins `planned` and `needs-fixes` as a build-phase state —
--      guarded by the SAME "has an approved plan" test that already qualifies
--      the other two. A story that failed BEFORE it was ever planned has no
--      approved plan and still routes to planning: the guard decides, not the
--      status.
--
--   2. The phase decision is RETURNED, with its reason, so the dispatch
--      surface can state it before anything is queued. "Planning 6 stories"
--      and "building 6 stories" are different enough that the manager should
--      never learn which one happened by reading the event log afterwards.
--
--   3. Belt and braces: if the inference lands on planning while EVERY story
--      holds an approved plan, dispatch refuses instead of proceeding. That
--      combination has no legitimate reading — it is the signature of this
--      bug, and it should be impossible to hit silently even if the inference
--      is wrong again in some way nobody has thought of.
--
-- Also stamps run_items.prev_issue_status (migration 144's column, US-27.1):
-- the status each story held before the run took it, so an unlanded or
-- cancelled story returns exactly where it was instead of falling back to a
-- guess.

create or replace function public.dispatch_feature_batch(p_feature uuid)
returns jsonb
language plpgsql
security invoker
set search_path = public
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
  if v_mode not in ('feature', 'epic') then
    raise exception 'batch dispatch needs build mode feature or epic (this project is "%")',
      v_mode;
  end if;

  select count(*) into v_children
  from public.issues c
  where c.parent_id = p_feature and c.abandoned_at is null;
  if v_children = 0 then
    raise exception 'feature has no stories to dispatch';
  end if;

  -- How many stories still lack an approved plan. Used twice: to decide the
  -- phase honestly, and to refuse the contradiction below.
  select count(*) into v_unplanned
  from public.issues c
  where c.parent_id = p_feature
    and c.abandoned_at is null
    and not exists (
      select 1 from public.artifacts a
      where a.issue_id = c.id and a.kind = 'plan' and a.status = 'approved'
    );

  -- US-27.11: `failed` is a build-phase state when a plan is already
  -- approved. The guard is the approved plan, not the status.
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
    -- The refusal. Planning every story while every story already holds an
    -- approved plan is the shape of the 2026-07-26 incident, and there is no
    -- state of the world in which it is what the manager meant.
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

    -- PLANNING: unchanged. One run per story, drained serially by rule (c).
    v_phase_reason := format(
      '%s of %s stories have no approved plan yet',
      v_unplanned, v_children);
    for v_child in
      select id, sub_no from public.issues
      where parent_id = p_feature and abandoned_at is null
      order by sub_no nulls last, created_at
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

  -- Every non-abandoned story must hold an approved plan before the feature
  -- builds. This is us-15.3's existing gate, not a new one — run_hold_reason
  -- already refuses to let a feature's code run start while any sibling is
  -- unplanned.
  --
  -- Refusing HERE rather than dispatching a subset is what keeps the two
  -- consistent: a run covering only the planned stories would be held forever
  -- by a story that is not in it, which is a deadlock with a queued run to
  -- explain. The manager plans the straggler and dispatches again.
  if v_unplanned > 0 then
    raise exception
      'not ready to build: % stor% in this feature still need% an approved plan',
      v_unplanned,
      case when v_unplanned = 1 then 'y' else 'ies' end,
      case when v_unplanned = 1 then 's' else '' end;
  end if;

  -- CODE: one run over every buildable story.
  --
  -- A story that cannot join is left out LOUDLY — never silently swept in
  -- with nothing for the agent to follow.
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
    order by c.sub_no nulls last, c.created_at
  loop
    if not v_child.has_plan then
      v_skipped := v_skipped || jsonb_build_object(
        'issue_id', v_child.id, 'reason', 'no approved implementation plan');
      continue;
    end if;
    -- US-27.11: `failed` joins the two statuses a build accepts. A story
    -- whose last build failed and whose plan is approved is exactly what a
    -- retry is for.
    if v_child.status not in ('planned', 'needs-fixes', 'failed') then
      v_skipped := v_skipped || jsonb_build_object(
        'issue_id', v_child.id,
        'reason', format('not buildable from status "%s"', v_child.status));
      continue;
    end if;

    v_position := v_position + 1;

    -- Rejection feedback is per story: a sent-back batch returns every
    -- included story to needs-fixes with its own comment, and the retry
    -- carries all of them.
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
      -- US-27.1: where this story goes back to if the run never lands a
      -- commit for it, or is cancelled.
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

  -- The brief stays compact the us-13.5 way: acceptance criteria inline (they
  -- are the contract the agent is judged against), approved plans pulled per
  -- story with get_context_detail. Five inlined plans would bury the criteria.
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

  -- Status moves on the STORIES, not only the feature: every surface that
  -- asks "is this story being built" reads the story's own status.
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
  'US-20.5 / US-22.9 / US-27.11: dispatch a feature. Planning is one run per '
  'story, drained serially. Coding is ONE run attached to the feature, with '
  'run_items naming the stories it covers. `failed` counts as a build-phase '
  'status when the story holds an approved plan, and the chosen phase is '
  'returned with its reason rather than inferred silently.';

-- ---------------------------------------------------------------------------
-- The phase, without dispatching anything (US-27.11)
-- ---------------------------------------------------------------------------
-- The dispatch surface has to say what it is about to do BEFORE it does it,
-- and the only honest way to answer that is with the same predicates the
-- dispatcher uses. Kept as its own function rather than duplicated in the
-- client, so the two cannot drift.
create or replace function public.feature_dispatch_phase(p_feature uuid)
returns jsonb
language sql
stable
security invoker
set search_path = public
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
  agg as (
    select count(*)::int as children,
           count(*) filter (where not has_plan)::int as unplanned,
           count(*) filter (
             where has_plan and status in ('planned', 'needs-fixes', 'failed')
           )::int as buildable
    from kids
  )
  select case
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
  end
  from agg
$$;

comment on function public.feature_dispatch_phase(uuid) is
  'US-27.11: which phase dispatch_feature_batch would run for this feature, '
  'and why — so the surface can state it before queueing anything.';
