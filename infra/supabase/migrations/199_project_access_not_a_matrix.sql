-- 199: a project is access, not a matrix (US-55.1).
--
-- The manager found the defect on 2026-07-30: the agent-level kind checkboxes
-- (runner_config.enabled_kinds, us-53.4) and the per-project capability matrix
-- (worker_capabilities, us-13.10/31.3) were two disconnected stores answering
-- the same question. Checking a kind on the agent changed nothing on any
-- project; granting a kind on a project ignored the agent's checkboxes. The
-- decision: project-level fine-tuning goes away. An agent's abilities are set
-- once, on the agent; a project row only says WHICH projects it may work.
--
-- worker_capabilities survives as the ACCESS store: exactly one row per
-- (worker, project), capability = 'access'. The historical per-kind rows
-- collapse — any existing row already meant "assigned to the project", so no
-- one gains or loses access here. What CAN widen is kinds: an agent that was
-- matrix-limited to (say) plan now does whatever its own checkboxes allow on
-- every project it can access. That is the requested inherit semantics.
--
-- worker_has_grant keeps its name and callers (the pool, the claim gate, the
-- clone gate — us-31.3's one shared predicate) and now answers:
--   access to the project AND (kind is null OR the agent's checkboxes allow it)
-- Fail-closed properties preserved: zero access rows -> nothing; a worker
-- with no runner_config row or a null enabled_kinds -> every kind (us-53.4's
-- no-backfill rule); enabled_kinds = [] -> benched.
--
-- run_kind_capability (178/185) is dropped: it existed to squeeze ten run
-- kinds into seven matrix columns, and enabled_kinds carries all ten.

-- 1. widen the vocabulary, so the canonical row is legal
alter table public.worker_capabilities
  drop constraint worker_capabilities_v2_capability_check;
alter table public.worker_capabilities
  add constraint worker_capabilities_v2_capability_check
  check (capability in ('access', 'prd', 'breakdown', 'plan', 'code', 'test',
                        'release', 'deploy'));

-- 2. normalize: one 'access' row per assigned (worker, project) pair.
-- The audit trigger stays quiet — this is a representation change, not a
-- thousand human revocations (150's backfill set the actor precedent; here
-- even that would mislead, so the trigger is off for the rewrite).
alter table public.worker_capabilities disable trigger user;

insert into public.worker_capabilities (org_id, worker_id, project_id, capability)
select distinct org_id, worker_id, project_id, 'access'
from public.worker_capabilities
on conflict (worker_id, project_id, capability) do nothing;

delete from public.worker_capabilities where capability <> 'access';

alter table public.worker_capabilities enable trigger user;

-- 3. the one predicate: access, then the agent's own checkboxes
create or replace function public.worker_has_grant(
  p_worker uuid, p_project uuid, p_capability text
) returns boolean
language sql stable as $$
  select exists (
    select 1 from public.worker_capabilities wc
    where wc.worker_id = p_worker
      and wc.project_id = p_project
  )
  and (
    p_capability is null
    or coalesce(
         (select rc.enabled_kinds is null or rc.enabled_kinds ? p_capability
          from public.runner_config rc
          where rc.worker_id = p_worker),
         true
       )
  );
$$;

-- 4. the seven-column mapping is dead; kinds are first-class on the agent
drop function if exists public.run_kind_capability(text);
