-- 259_pending_count_counts_the_feature_once (us-96.7): the badge counts
-- triage units, not rows the page no longer shows.
--
-- The Workbench now collapses a feature's waiting children into ONE
-- synthesized feature row (feature-rollup.ts), so the shell badge must
-- count the same way — the badge disagreeing with the page it links to is
-- the exact failure US-87.2 replaced.
--
-- Mechanically: the four issue-backed groups (Reviews, Fix & retry,
-- Dispatch, Triage) collapse into one `count(distinct coalesce(parent_id,
-- id))` over the union of their predicates. The predicates themselves are
-- byte-carried from 249 — a planned feature-child is still excluded (the
-- feature builds it), a ready feature is still never a dispatch row — and
-- the four statuses sets are disjoint, so the only behavioral change is
-- the collapse. Non-issue groups (sign-offs, recommendations, refreshes,
-- clarifications) are untouched.
--
-- Mirrors loadWaiting().pendingCount AFTER rollupFeatureRows() in
-- apps/web/src/app/(app)/workbench/data.ts — CHANGE BOTH TOGETHER.

create or replace function public.org_pending_count(p_org uuid)
returns integer
language plpgsql
stable
security definer
set search_path = public
as $$
declare
  v_total integer;
begin
  if not public.is_org_member(p_org) then
    return 0;
  end if;

  with waiting as (
    select i.id,
           i.status,
           i.type,
           i.parent_id,
           coalesce(p.build_mode, 'story') as build_mode
      from public.issues i
      join public.projects p
        on p.id = i.project_id and p.org_id = i.org_id
     where i.org_id = p_org
       and i.abandoned_at is null
       and p.archived_at is null
       and i.status in ('prd-review','plan-review','in-review',
                        'needs-fixes','failed','planned','ready','draft')
       and not exists (
             select 1 from public.runs r
              where r.issue_id = i.id
                and r.org_id = p_org
                and r.kind in ('prd','breakdown')
                and r.status in ('queued','running')
           )
  ),
  -- us-96.7: Reviews + Fix & retry + Dispatch + Triage, counted as UNITS —
  -- a feature's children collapse to the feature. Predicates carried from
  -- 249 unchanged.
  issue_units as (
    select count(distinct coalesce(w.parent_id, w.id)) as n
      from waiting w
     where w.status in ('prd-review','plan-review','in-review',
                        'needs-fixes','failed','draft')
        or (
             w.status = 'planned'
             and not (w.build_mode in ('feature','epic') and w.parent_id is not null)
           )
        or (
             w.status = 'ready'
             and w.type <> 'feature'
             and not exists (
                   select 1 from public.issues c
                    where c.parent_id = w.id
                      and c.abandoned_at is null
                 )
           )
  ),
  signoffs as (
    select count(*) as n
      from public.releases rel
      join public.projects p
        on p.id = rel.project_id and p.org_id = rel.org_id
     where rel.org_id = p_org
       and rel.status = 'uat-deployed'
       and p.archived_at is null
  ),
  recs as (
    select count(*) as n
      from public.guideline_recommendations gr
      join public.projects p
        on p.id = gr.project_id and p.org_id = gr.org_id
     where gr.org_id = p_org
       and gr.status = 'pending'
       and gr.refresh_id is null
       and p.archived_at is null
  ),
  refreshes as (
    select count(*) as n
      from public.guideline_refreshes gf
      join public.projects p
        on p.id = gf.project_id and p.org_id = gf.org_id
     where gf.org_id = p_org
       and gf.status = 'pending'
       and p.archived_at is null
       and exists (
             select 1 from public.guideline_recommendations x
              where x.refresh_id = gf.id
           )
  ),
  clarifs as (
    select count(*) as n
      from public.clarifications c
      join public.issues i on i.id = c.issue_id
      join public.projects p
        on p.id = i.project_id and p.org_id = i.org_id
     where c.org_id = p_org
       and c.answered_at is null
  )
  select (issue_units.n + signoffs.n + recs.n + refreshes.n + clarifs.n)::int
    into v_total
    from issue_units, signoffs, recs, refreshes, clarifs;

  return coalesce(v_total, 0);
end;
$$;

comment on function public.org_pending_count(uuid) is
  'US-87.2 / us-96.7: the shell badge count. Mirrors '
  'loadWaiting().pendingCount AFTER rollupFeatureRows() in '
  'apps/web/src/app/(app)/workbench/data.ts — a feature''s waiting '
  'children count as ONE unit. Change both together.';
