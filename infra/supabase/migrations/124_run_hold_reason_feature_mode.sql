-- 124_run_hold_reason_feature_mode: centralize pool eligibility + Build-by-Feature (US-17.2).
--
-- The us-15.3 sibling-draft hold was copy-pasted across claim_run,
-- list_worker_pool and list_factory_queue. This replaces those copies with one
-- function, run_hold_reason(run) -> text (null = claimable, else the reason),
-- and adds the `feature` build-mode rules on top:
--   (a) a later feature is held entirely until the earlier feature is `done`
--       (features drain in number order: epic number, then feature item_no);
--   (b) a story's CODE run is held until every non-abandoned sibling in the
--       feature has an approved plan (plan the whole feature before coding any).
-- `story` mode keeps exactly today's behaviour (only the us-15.3 rule applies).
-- `epic` mode is added in us-17.3 (this function is replaced there).

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
  -- Project-scoped runs (release/deploy) and unknown runs are never held here.
  if not found or v_run.issue_id is null then
    return null;
  end if;
  select * into v_issue from public.issues where id = v_run.issue_id;
  if not found then
    return null;
  end if;

  -- US-15.3 (always on, every mode): a story run is held while any
  -- non-abandoned sibling is still draft (the split isn't curated yet).
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
    -- The feature this run belongs to: the issue itself if it's a feature,
    -- else its parent. A standalone story (no parent, not a feature) has no
    -- feature batch and is only subject to the us-15.3 rule above.
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

      -- (a) hold everything for this feature/its stories while an earlier
      -- feature (project-wide order: epic number, then feature item_no) is
      -- not yet done. Abandoned features never block.
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

      -- (b) a code run waits until the whole feature is planned: every
      -- non-abandoned sibling has an approved plan artifact.
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
  end if;

  return null;
end;
$$;
