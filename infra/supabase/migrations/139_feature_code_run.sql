-- 139: a feature's coding phase is ONE run (US-22.9).
--
-- us-20.5 made dispatch_feature_batch loop over the children calling
-- dispatch_issue, and rule (c) then drained them one at a time. That is right
-- for PLANNING, where each story genuinely needs its own thought. It is wrong
-- for BUILDING: story 2's agent re-derives the abstraction story 1 chose,
-- story 4's agent refactors it, and the manager reviews the same file five
-- times.
--
-- Planning is unchanged — still one run per story, still drained serially.
-- Only the code phase collapses into a single run attached to the FEATURE,
-- with run_items naming the stories it covers in sub_no order.
--
-- This also resolves the interaction with migration 137: dispatch_issue now
-- refuses a story-level code run in feature/epic mode, so the old loop could
-- not have dispatched a code phase at all. The code phase no longer goes
-- through dispatch_issue.
--
-- THE COST, STATED PLAINLY: one PR merges or it does not. The manager cannot
-- approve stories 1-3 and reject 4-5, because there is one commit. That is
-- the honest consequence of one change, one review — and it is why this is
-- opt-in through a mode the manager already sets. `story` mode is the answer
-- for per-story gates.

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

  if not exists (
    select 1 from public.issues c
    where c.parent_id = p_feature and c.abandoned_at is null
  ) then
    raise exception 'feature has no stories to dispatch';
  end if;

  -- The phase is inferred from the children's rolled-up state, exactly as
  -- before: if any child is ready to be built, this is the code phase.
  select exists (
    select 1
    from public.issues c
    where c.parent_id = p_feature
      and c.abandoned_at is null
      and c.status in ('planned', 'needs-fixes')
      and exists (
        select 1 from public.artifacts a
        where a.issue_id = c.id and a.kind = 'plan' and a.status = 'approved'
      )
  ) into v_code_phase;

  if not v_code_phase then
    -- PLANNING: unchanged. One run per story, drained serially by rule (c).
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

    return jsonb_build_object('dispatched', v_dispatched, 'skipped', v_skipped);
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
  select count(*) into v_position
  from public.issues c
  where c.parent_id = p_feature
    and c.abandoned_at is null
    and not exists (
      select 1 from public.artifacts a
      where a.issue_id = c.id and a.kind = 'plan' and a.status = 'approved'
    );
  if v_position > 0 then
    raise exception
      'not ready to build: % stor% in this feature still need% an approved plan',
      v_position,
      case when v_position = 1 then 'y' else 'ies' end,
      case when v_position = 1 then 's' else '' end;
  end if;
  v_position := 0;

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
    if v_child.status not in ('planned', 'needs-fixes') then
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

  insert into public.run_items (run_id, issue_id, org_id, position)
  select v_run,
         (s->>'issue_id')::uuid,
         v_feature.org_id,
         (s->>'position')::int
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
    'story_count', v_position);
end;
$$;

comment on function public.dispatch_feature_batch(uuid) is
  'US-20.5 / US-22.9: dispatch a feature. Planning is one run per story, '
  'drained serially. Coding is ONE run attached to the feature, with '
  'run_items naming the stories it covers.';
