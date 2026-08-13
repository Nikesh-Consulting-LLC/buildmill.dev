-- 129_feature_batch_dispatch_and_serial_drain: US-20.5.
--
-- US-17.2 made `feature` mode routing-only: run_hold_reason holds runs, but
-- nothing dispatches a feature's stories as a batch and dispatch_issue
-- explicitly refuses a feature that has children. This adds the missing half.
--
--   * dispatch_feature_batch(feature) — queue a run for every child story, in
--     sub_no order, by calling dispatch_issue per child inside a nested
--     exception block so one non-dispatchable child cannot abort the batch.
--   * issue_in_trouble(issue) — the shared definition of "this story needs the
--     manager", used by the pause rule below.
--   * run_hold_reason gains two rules for feature/epic mode:
--       (c) one in flight — a story's run waits while an EARLIER sibling
--           (lower sub_no) has a run of the same kind queued or running. It
--           holds on the RUN, not on the manager's approval, so story 2 starts
--           the moment story 1 hands back and review happens in parallel.
--       (d) trouble pauses the feature — while any sibling is in trouble every
--           OTHER story's run in that feature is held.
--
-- Rule (d) exempts a troubled story's own run, and that exemption is load
-- bearing: without it the fix run for the story that broke the batch would be
-- held by its own breakage, and two troubled stories would hold each other
-- forever. Remediation always flows; only healthy stories pause.
--
-- run_hold_reason is rebuilt from its CURRENT definition (125) with the epic
-- and feature blocks carried verbatim — never from an older migration's body.
-- Migrations 095/105/106 record that lesson being learned twice.

-- ---------------------------------------------------------------------------
-- Is this story waiting on the manager?
-- ---------------------------------------------------------------------------

create or replace function public.issue_in_trouble(p_issue uuid)
returns boolean
language sql
stable
as $$
  select exists (
    select 1 from public.issues i
    where i.id = p_issue
      and i.abandoned_at is null
      and (
        i.status in ('failed', 'needs-fixes')
        -- A sent-back plan returns the issue to `ready` (send_back_plan
        -- restores the pre-dispatch status), which is otherwise
        -- indistinguishable from never-dispatched. The gate decision is the
        -- only durable record that it went backwards.
        or (
          (
            select a.decision from public.approvals a
            where a.issue_id = i.id and a.gate = 'plan'
            order by a.created_at desc limit 1
          ) = 'sent-back'
          and not exists (
            select 1 from public.artifacts ar
            where ar.issue_id = i.id
              and ar.kind = 'plan' and ar.status = 'approved'
          )
        )
      )
  );
$$;

-- ---------------------------------------------------------------------------
-- run_hold_reason — 125 verbatim, plus rules (c) and (d)
-- ---------------------------------------------------------------------------

create or replace function public.run_hold_reason(p_run uuid)
returns text
language plpgsql
stable
as $$
declare
  v_run public.runs%rowtype;
  v_issue public.issues%rowtype;
  v_mode text;
  v_feature uuid;
  v_epic uuid;
  v_feat_no int;
  v_epic_no int;
  v_cnt int;
  v_blocker text;
begin
  select * into v_run from public.runs where id = p_run;
  if not found or v_run.issue_id is null then
    return null;
  end if;
  select * into v_issue from public.issues where id = v_run.issue_id;
  if not found then
    return null;
  end if;

  -- US-15.3 (always on): a story run is held while any non-abandoned sibling
  -- is still draft (the split isn't curated yet).
  if v_issue.parent_id is not null then
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

  select build_mode into v_mode from public.projects where id = v_run.project_id;

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

      if exists (
        select 1
        from public.issues f
        join public.epics fe on fe.id = f.epic_id
        where f.project_id = v_run.project_id
          and f.type = 'feature'
          and f.abandoned_at is null
          and f.status <> 'done'
          and f.id <> v_feature
          and (fe.number, f.item_no) < (v_epic_no, v_feat_no)
      ) then
        return 'waiting on an earlier feature to finish';
      end if;

      if v_run.kind = 'code' then
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
      if v_run.kind = 'plan' then
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
      elsif v_run.kind = 'code' then
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
        and r.kind = v_run.kind
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
$$;

-- ---------------------------------------------------------------------------
-- Dispatch every story in a feature, in id order
-- ---------------------------------------------------------------------------

create or replace function public.dispatch_feature_batch(p_feature uuid)
returns jsonb
language plpgsql
as $$
declare
  v_feature public.issues%rowtype;
  v_mode text;
  v_child record;
  v_run uuid;
  v_kind text;
  v_dispatched jsonb := '[]'::jsonb;
  v_skipped jsonb := '[]'::jsonb;
begin
  select * into v_feature from public.issues where id = p_feature;
  if not found then
    raise exception 'feature not found';
  end if;
  if v_feature.type <> 'feature' then
    raise exception 'batch dispatch applies to a feature, not a %', v_feature.type;
  end if;
  if v_feature.abandoned_at is not null then
    raise exception 'feature is abandoned';
  end if;

  select build_mode into v_mode from public.projects where id = v_feature.project_id;
  if coalesce(v_mode, 'story') not in ('feature', 'epic') then
    raise exception 'batch dispatch needs build mode feature or epic (this project is "%")',
      coalesce(v_mode, 'story');
  end if;

  if not exists (
    select 1 from public.issues c
    where c.parent_id = p_feature and c.abandoned_at is null
  ) then
    raise exception 'feature has no stories to dispatch';
  end if;

  -- sub_no is the readable story id's last segment and the order breakdown
  -- created them in (dependency order), so it IS the queue order the manager
  -- already reads on screen.
  for v_child in
    select id, sub_no from public.issues
    where parent_id = p_feature and abandoned_at is null
    order by sub_no nulls last, created_at
  loop
    -- A nested block is a subtransaction: a child that is not dispatchable
    -- from its current status is skipped and reported, not fatal.
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
end;
$$;

grant execute on function public.issue_in_trouble(uuid) to authenticated;
grant execute on function public.dispatch_feature_batch(uuid) to authenticated;
