-- 125_run_hold_reason_epic_mode: Build by Epic (US-17.3).
--
-- Widens run_hold_reason from the feature batch (124) to the epic: in `epic`
-- mode a whole epic moves document -> plan -> build as one unit.
--   - a PLAN run is held while any feature in the epic isn't broken down yet
--     (the document phase must finish first);
--   - a CODE run is held while any non-abandoned story in the epic lacks an
--     approved plan (the plan phase must finish before any build).
-- PRD/breakdown runs (the document phase itself) are never held by the mode.
-- A feature with no epic is treated as its own singleton batch (not held by the
-- epic-wide rules). `story` and `feature` modes are unchanged.

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

  return null;
end;
$$;
