-- 249_org_pending_count.sql
--
-- US-87.2: the sidebar badge stops costing the whole Things-to-Do dataset.
--
-- `loadWaiting` fetches every waiting issue with its epic and project joins,
-- every UAT release, every active PRD run, every open clarification, every
-- pending recommendation, every refresh, every parked run and every dispatch
-- block — then reduces all of it to one integer for the shell badge. It was
-- the most-executed application statement in the database (19,967 calls /
-- 134 s over six weeks measured on prod, 2026-08-12) and it scales with the
-- size of the workspace rather than the size of the answer.
--
-- This function is that same definition, counted in the database. It mirrors
-- `loadWaiting` in apps/web/src/app/(app)/dashboard/data.ts group for group,
-- and the comment on each CTE names the group it answers. THE TWO MUST BE
-- CHANGED TOGETHER — a badge that disagrees with the page header it links to
-- is the failure this replaces, not an acceptable cost of making it fast.
-- `tests/test_org_pending_count_sql.py` is what proves they still agree.

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
  -- Vault-style member gate: `security definer` runs as the owner, so the
  -- membership check that RLS would have applied has to be explicit. A
  -- non-member counting another workspace's backlog is a leak, not a badge.
  if not public.is_org_member(p_org) then
    return 0;
  end if;

  with waiting as (
    -- `loadWaiting`'s `waiting`: a work item in a manager-facing status, in a
    -- live project, with no PRD/breakdown run already in flight for it (an
    -- item the factory is actively working is not waiting on anyone).
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
  -- Group "Reviews": every waiting item at a review gate.
  reviews as (
    select count(*) as n from waiting
     where status in ('prd-review','plan-review','in-review')
  ),
  -- Group "QA sign-offs": a release sitting on UAT (US-21.7). One per
  -- release, unconditional — the test gate decides what the row SAYS, not
  -- whether it counts.
  signoffs as (
    select count(*) as n
      from public.releases rel
      join public.projects p
        on p.id = rel.project_id and p.org_id = rel.org_id
     where rel.org_id = p_org
       and rel.status = 'uat-deployed'
       and p.archived_at is null
  ),
  -- Group "Fix & retry".
  fixes as (
    select count(*) as n from waiting where status in ('needs-fixes','failed')
  ),
  -- Group "Dispatch". US-22.10: a `planned` story in a feature/epic-mode
  -- project is built BY its feature, so it is not the manager's to dispatch.
  -- US-14.6: a `ready` feature is never planned directly, and a `ready` item
  -- whose children already exist has been broken down already.
  dispatch as (
    select count(*) as n
      from waiting w
     where (
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
  -- Group "Triage".
  triage as (
    select count(*) as n from waiting where status = 'draft'
  ),
  -- US-5.32 ad-hoc guideline recommendations. US-43.3: bundled rows belong to
  -- their refresh's single card below, not one count each.
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
  -- US-43.3: one count per open refresh, and only once the agent has handed
  -- sections back — `pending` also covers "still running", which is not a
  -- review waiting on anyone (`ready: recs.length > 0` in data.ts).
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
  -- US-5.4 open worker questions. Mirrors the page's inner joins: a
  -- clarification whose issue or project is gone is not a question anyone
  -- can answer.
  clarifs as (
    select count(*) as n
      from public.clarifications c
      join public.issues i on i.id = c.issue_id
      join public.projects p
        on p.id = i.project_id and p.org_id = i.org_id
     where c.org_id = p_org
       and c.answered_at is null
  )
  select (reviews.n + signoffs.n + fixes.n + dispatch.n
          + triage.n + recs.n + refreshes.n + clarifs.n)::int
    into v_total
    from reviews, signoffs, fixes, dispatch, triage, recs, refreshes, clarifs;

  return coalesce(v_total, 0);
end;
$$;

comment on function public.org_pending_count(uuid) is
  'US-87.2: the shell badge count. Mirrors loadWaiting().pendingCount in '
  'apps/web/src/app/(app)/dashboard/data.ts — change both together.';

revoke all on function public.org_pending_count(uuid) from public;
grant execute on function public.org_pending_count(uuid) to authenticated;

-- US-87.3: the work-item hub orders by updated_at desc and filters to the
-- selected projects. `issues_active_idx (project_id) WHERE abandoned_at IS
-- NULL` covers the filter but not the sort, so every hub load sorted the
-- whole set.
create index if not exists issues_project_updated_idx
  on public.issues (project_id, updated_at desc)
  where abandoned_at is null;
