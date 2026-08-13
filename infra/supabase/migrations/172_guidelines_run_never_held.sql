-- 172_guidelines_run_never_held: US-43.5.
--
-- run_hold_reason(run) is the single gate list_worker_pool and claim_run both
-- consult; null means claimable. This rebuild is 129's body VERBATIM with one
-- rule added at the top -- the guidelines exemption -- and nothing else
-- touched. 095/105/106 record what rebuilding from an older body costs.
--
-- The OTHER half of US-43.5 is not here and cannot be: what actually blocks a
-- refresh today is queue POSITION, not a hold. list_worker_pool and claim_run
-- order by `queue_rank asc nulls last, created_at asc` (118), and a new run's
-- queue_rank is null -- which sorts last, exactly as US-15.2 intended for
-- delivery work. Behind fifteen queued story runs a refresh is offered
-- sixteenth on a serially draining queue. The dispatch endpoint sets
-- queue_rank = -1 instead, ahead of both unranked runs and the manager's own
-- ordering. No new branch in either order-by: the lever already exists.

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

  -- US-43.5: a guidelines refresh is not delivery work. It orders after
  -- nothing and nothing orders after it, so no rule below should reach it.
  --
  -- Every rule below already misses it -- the us-15.3 rule needs a parent,
  -- the feature block needs `type = 'feature'` or a parent, the epic block
  -- needs an epic_id and tests kind against plan/code, and a parentless
  -- chore of this kind escapes all four. That is FOUR accidents, none of
  -- them written down. This function has been rebuilt five times (124, 125,
  -- 129, 139, 146) and the practice is to carry the blocks forward verbatim;
  -- stated here, at the top, the exemption gets carried forward with them.
  -- Left implicit, the sixth rebuild takes it away and the symptom is a
  -- refresh queued forever behind a hold reason about features.
  if v_run.kind = 'guidelines' then
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
