-- 150_fail_closed_grants: an agent only works on projects it is assigned to
-- (US-31.3).
--
-- The capability gate was fail-OPEN by design: zero grant rows meant
-- "unrestricted", at all three sites — the pool listing, the claim gate, and
-- the git-proxy clone/fetch gate. So a freshly provisioned agent (exactly
-- what add_slot produces before anyone touches the matrix) could list the
-- pool of every project in the org, claim any run, and clone any repository.
-- It also made the app contradict itself: US-27.9 reports `no-grants` as a
-- reason an agent CANNOT work, while the gate treated it as permission for
-- everything.
--
-- Two parts:
--   1. ONE predicate — worker_has_grant() — for all three gates, so they can
--      never drift apart again (the clone gate is the one nobody was
--      thinking about).
--   2. The backfill: every zero-grant worker receives every capability on
--      every existing project, so nothing that was working stops working
--      when the gate inverts. This deliberately writes today's
--      over-permission into explicit rows the manager can prune. The audit
--      rows say `migration/backfill`, so the trail never claims a human
--      granted them.

-- 1. The shared predicate ---------------------------------------------------

create or replace function public.worker_has_grant(
  p_worker uuid, p_project uuid, p_capability text
) returns boolean
language sql
stable
set search_path = ''
as $$
  select exists (
    select 1 from public.worker_capabilities wc
    where wc.worker_id = p_worker
      and wc.project_id = p_project
      and (p_capability is null or wc.capability = p_capability)
  );
$$;

comment on function public.worker_has_grant(uuid, uuid, text) is
  'US-31.3: THE capability predicate — pool listing, claim gate and git-proxy '
  'read gate all call this. Null capability asks "any grant on the project" '
  '(the clone gate). Fail-closed: no rows means false. If a capability rule '
  'ever changes, it changes here, everywhere, at once.';

-- 2. The backfill -----------------------------------------------------------

with zero_grant as (
  select w.id as worker_id, w.org_id
  from public.workers w
  where not exists (
    select 1 from public.worker_capabilities wc where wc.worker_id = w.id
  )
),
minted as (
  insert into public.worker_capabilities (org_id, worker_id, project_id, capability)
  select z.org_id, z.worker_id, p.id, c.cap
  from zero_grant z
  join public.projects p on p.org_id = z.org_id
  cross join (values
    ('prd'), ('breakdown'), ('plan'), ('code'), ('test'), ('release'), ('deploy')
  ) as c(cap)
  on conflict (worker_id, project_id, capability) do nothing
  returning org_id, worker_id, project_id, capability
)
insert into public.worker_capability_events (org_id, worker_id, actor, event, detail)
select m.org_id, m.worker_id, 'migration/backfill', 'granted',
       jsonb_build_object(
         'project_id', m.project_id,
         'capability', m.capability,
         'reason', 'US-31.3 fail-closed inversion — preserving prior fail-open behaviour'
       )
from minted m;
