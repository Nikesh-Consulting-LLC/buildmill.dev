-- 247_two_switches_serial_law (US-86.1): routing is two checkboxes;
-- execution is always serial.
--
-- The manager's redesign. The build-mode radio and the Concurrency checkbox
-- retire; in their place:
--
--   projects.follow_build_order    (switch 1, default ON) — items go in
--     Epic → Feature → Story order. OFF frees the ORDER only.
--   projects.route_feature_as_one  (switch 2, default ON) — the feature is
--     the routing unit: batch plan, one feature-owned code run/PR, no
--     per-story route buttons.
--
-- And one law with no checkbox: A PROJECT WORKS ONE ITEM AT A TIME, START
-- TO MERGE. The routing unit (story, or feature under switch 2) owns the
-- project from its first claimed run until its work merges — nothing else
-- starts while it is being planned, awaiting plan approval, holding an
-- approved plan, being built, or sitting unmerged. Everything queued behind
-- it is HELD (soft, hourglass), never refused. sequential_only's dispatch-
-- time freeze — which blocked *queueing* and hid why (2026-08-12) — is
-- deleted outright.
--
-- Legacy columns: build_mode and sequential_only are KEPT but demoted to
-- mirrors — a trigger derives build_mode from route_feature_as_one
-- ('feature'/'story'; 'epic' collapses to 'feature') and pins
-- sequential_only false, so the not-yet-rewritten readers
-- (dispatch_feature_batch, feature_dispatch_phase, run_work_units, 146,
-- 189) keep behaving correctly until a cleanup migration retires them.
-- Nothing may write build_mode/sequential_only directly anymore.

-- 1 ------------------------------------------------------------------------
-- The two switches, and the value migration (US-86.1 AC6):
--   build_mode feature/epic -> both switches on; story -> switch 2 off.
--   sequential_only -> dropped without replacement (pinned false).

alter table public.projects
  add column if not exists follow_build_order boolean not null default true,
  add column if not exists route_feature_as_one boolean not null default true;

update public.projects
set follow_build_order = true,
    route_feature_as_one = (coalesce(build_mode, 'story') in ('feature', 'epic'));

create or replace function public.projects_mirror_legacy_routing()
returns trigger
language plpgsql
as $function$
begin
  -- The mirrors are derived, never authored: old readers see a coherent
  -- world, old writers (a stale UI radio) are silently corrected.
  new.build_mode := case when new.route_feature_as_one then 'feature' else 'story' end;
  new.sequential_only := false;
  return new;
end;
$function$;

drop trigger if exists projects_mirror_legacy_routing on public.projects;
create trigger projects_mirror_legacy_routing
  before insert or update on public.projects
  for each row execute function public.projects_mirror_legacy_routing();

-- Re-run the mirror over existing rows so build_mode/sequential_only are
-- consistent with the switches from this moment on.
update public.projects set follow_build_order = follow_build_order;

-- 2 ------------------------------------------------------------------------
-- The hold rules, rebuilt around the law. Kept from 235: the guidelines
-- exemption, the curation rule (siblings still draft), the earlier-feature
-- ordering hold (now gated on switch 1), the trouble rule (d) (switch 1),
-- and the sibling-plans-before-code hold (now gated on switch 2). Gone:
-- rule (c) (subsumed by the law) and the epic-mode branches (an epic is
-- ordering, not a routing unit).

create or replace function public.issue_hold_reason(p_issue uuid, p_kind text)
returns text
language plpgsql
stable
as $function$
declare
  v_issue public.issues%rowtype;
  v_follow boolean;
  v_featone boolean;
  v_unit uuid;
  v_feature uuid;
  v_epic uuid;
  v_feat_no int;
  v_epic_no int;
  v_my_epic_no int;
  v_my_queued timestamptz;
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

  -- US-43.5: a guidelines refresh is not delivery work; no rule reaches it.
  if p_kind = 'guidelines' then
    return null;
  end if;

  -- US-15.3: a story run is held while any non-abandoned sibling is still
  -- draft. US-44.1: elaborate/wireframe exempt (they fix that condition).
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

  select coalesce(follow_build_order, true), coalesce(route_feature_as_one, true)
    into v_follow, v_featone
  from public.projects where id = v_issue.project_id;

  -- My routing unit: the feature when switch 2 groups stories, else myself.
  v_unit := case
    when v_featone and v_issue.parent_id is not null then v_issue.parent_id
    else v_issue.id
  end;

  -- US-86.1, the law: one unit in progress, start to merge. Another unit
  -- anywhere between its first claim and its merge holds everything —
  -- including an approved plan parked awaiting the build, which is the
  -- manager's own gate to clear. 'failed' does NOT hold: a failed attempt
  -- ended its journey until the manager redispatches it.
  select
      coalesce(
        case when ep.number is not null and i.item_no is not null then
          case when i.type = 'feature' then 'FEAT-' || ep.number || '.' || i.item_no
               else (case i.type when 'bug' then 'BUG-' when 'chore' then 'CHORE-' else 'US-' end)
                    || ep.number || '.' || i.item_no
                    || coalesce('.' || i.sub_no, '')
          end
        end,
        i.title)
      || case i.status
           when 'plan-review' then ' is awaiting your plan approval'
           when 'planned' then ' holds an approved plan awaiting build'
           when 'in-review' then '''s PR is not merged yet'
           when 'needs-fixes' then '''s PR is not merged yet'
           else ' is being built'
         end
    into v_blocker
  from public.issues i
  left join public.epics ep on ep.id = i.epic_id
  where i.project_id = v_issue.project_id
    and i.abandoned_at is null
    and (case when v_featone and i.parent_id is not null then i.parent_id else i.id end) <> v_unit
    and i.status in ('planning', 'plan-review', 'planned', 'running',
                     'in-review', 'needs-fixes')
  order by i.updated_at asc
  limit 1;
  if v_blocker is not null then
    return 'waiting: ' || v_blocker;
  end if;

  -- Among queued units with nothing in progress, exactly one is offerable:
  -- the first in build order (switch 1 on) or in dispatch order (off).
  -- Compared only when I am queued myself — before dispatch there is no
  -- queue position to lose.
  if v_issue.status = 'queued' then
    if v_follow then
      select number into v_my_epic_no from public.epics where id = v_issue.epic_id;
      select coalesce(
               case when ep.number is not null and i.item_no is not null then
                 case when i.type = 'feature' then 'FEAT-' || ep.number || '.' || i.item_no
                      else (case i.type when 'bug' then 'BUG-' when 'chore' then 'CHORE-' else 'US-' end)
                           || ep.number || '.' || i.item_no
                           || coalesce('.' || i.sub_no, '')
                 end
               end,
               i.title)
        into v_blocker
      from public.issues i
      left join public.epics ep on ep.id = i.epic_id
      where i.project_id = v_issue.project_id
        and i.abandoned_at is null
        and i.status = 'queued'
        and (case when v_featone and i.parent_id is not null then i.parent_id else i.id end) <> v_unit
        and (coalesce(ep.number, 2147483647),
             coalesce(i.item_no, 2147483647),
             coalesce(i.sub_no, 2147483647),
             i.created_at)
          < (coalesce(v_my_epic_no, 2147483647),
             coalesce(v_issue.item_no, 2147483647),
             coalesce(v_issue.sub_no, 2147483647),
             v_issue.created_at)
      order by coalesce(ep.number, 2147483647),
               coalesce(i.item_no, 2147483647),
               coalesce(i.sub_no, 2147483647),
               i.created_at
      limit 1;
    else
      select min(r.created_at) into v_my_queued
      from public.runs r
      join public.issues ii on ii.id = r.issue_id
      where r.status = 'queued'
        and ii.project_id = v_issue.project_id
        and (case when v_featone and ii.parent_id is not null then ii.parent_id else ii.id end) = v_unit;

      if v_my_queued is not null then
        select coalesce(
                 case when ep.number is not null and i.item_no is not null then
                   case when i.type = 'feature' then 'FEAT-' || ep.number || '.' || i.item_no
                        else (case i.type when 'bug' then 'BUG-' when 'chore' then 'CHORE-' else 'US-' end)
                             || ep.number || '.' || i.item_no
                             || coalesce('.' || i.sub_no, '')
                   end
                 end,
                 i.title)
          into v_blocker
        from public.issues i
        left join public.epics ep on ep.id = i.epic_id
        join public.runs r on r.issue_id = i.id and r.status = 'queued'
        where i.project_id = v_issue.project_id
          and i.abandoned_at is null
          and (case when v_featone and i.parent_id is not null then i.parent_id else i.id end) <> v_unit
          and (r.created_at, i.id) < (v_my_queued, v_issue.id)
        order by r.created_at, i.id
        limit 1;
      end if;
    end if;
    if v_blocker is not null then
      return format('waiting: %s is ahead in the queue', v_blocker);
    end if;
  end if;

  -- Switch 1: hierarchy ordering — an earlier feature that isn't done yet
  -- goes first. Carried from 235, gated on the switch instead of the mode.
  if v_follow then
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
    end if;

    -- (d) trouble pauses healthy siblings — never the troubled story's own
    -- remediation (US-86.1 AC7, carried from 129/235).
    if v_issue.parent_id is not null and not public.issue_in_trouble(v_issue.id) then
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
  end if;

  -- Switch 2: the feature codes as one run, so every sibling's plan must be
  -- approved first. Carried from 235, gated on the switch.
  if v_featone and p_kind = 'code' then
    if v_issue.type = 'feature' then
      v_feature := v_issue.id;
    else
      v_feature := v_issue.parent_id;
    end if;
    if v_feature is not null then
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

  return null;
end;
$function$;

comment on function public.issue_hold_reason(uuid, text) is
  'Why a run of this kind for this work item would be held by the pool, or '
  'null if nothing holds it. US-86.1: one unit in progress per project, '
  'start to merge; switch 1 orders the queue, switch 2 sets the unit. '
  'run_hold_reason wraps this for an existing run.';

-- 3 ------------------------------------------------------------------------
-- The dispatch-time refusal: sequential_only's "must reach merged" refusal
-- is DELETED — queueing is always legal. The feature-owns-the-build refusal
-- stays, keyed to switch 2.

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

  return null;
end;
$function$;

comment on function public.issue_dispatch_refusal(uuid, text) is
  'The message dispatch_issue would raise for this work item and kind, or '
  'null if it would be accepted. US-86.1 deleted the sequential-only '
  'refusal: dispatch is always legal; the serial law holds at claim time.';
