-- 178_run_kind_maps_to_a_capability: a blocking defect in Phases 43 and 44.
--
-- All three capability gates — pool listing, claim, and the git-proxy read
-- gate — call `worker_has_grant(worker, project, r.kind)`, passing the RUN
-- KIND straight in as the capability name. That works only while the two
-- vocabularies are the same word, which they were for the seven stages the
-- US-13.10 matrix names: prd, breakdown, plan, code, test, release, deploy.
--
-- US-43.1 added the `guidelines` kind and US-44.1 added `elaborate`. Both
-- stories decided, in as many words, to gate on the EXISTING `plan`
-- capability — "an eighth column on the matrix would cost a migration, a UI
-- change and a backfill decision to express a permission the matrix can
-- already express". Neither implemented the mapping.
--
-- The result: `wc.capability = 'guidelines'` matches nothing, the gate is
-- fail-closed (US-31.3), and so a guidelines or elaborate run can never be
-- claimed by anyone. The manager sees "Nobody can take this" on the queue and
-- the run sits there forever. Nothing is misconfigured — the capability the
-- gate asks for is not one the matrix is able to grant.
--
-- The fix belongs in `worker_has_grant` and nowhere else. Migration 150 made
-- it the ONE predicate precisely so the three gates could not drift; putting
-- the mapping at each call site would recreate the drift it removed. The
-- matrix vocabulary is deliberately NOT widened: `guidelines` and `elaborate`
-- are read-only, repo-reading, non-code runs, which is what a `plan` grant
-- already means.
--
-- A null capability still means "any grant on this project" — the clone gate.

create or replace function public.run_kind_capability(p_kind text)
returns text
language sql
immutable
as $$
  select case p_kind
    -- Read the repository, write no code: exactly what `plan` grants.
    when 'guidelines' then 'plan'
    when 'elaborate'  then 'plan'
    else p_kind
  end;
$$;

comment on function public.run_kind_capability(text) is
  'US-43.1/US-44.1: maps a runs.kind to the worker_capabilities.capability '
  'that gates it. Most kinds are their own capability; the read-only kinds '
  'that the US-13.10 matrix has no column for ride the `plan` grant rather '
  'than widening the matrix.';

create or replace function public.worker_has_grant(
  p_worker uuid,
  p_project uuid,
  p_capability text
)
returns boolean
language sql
stable
as $$
  select exists (
    select 1 from public.worker_capabilities wc
    where wc.worker_id = p_worker
      and wc.project_id = p_project
      and (
        p_capability is null
        or wc.capability = public.run_kind_capability(p_capability)
      )
  );
$$;

comment on function public.worker_has_grant(uuid, uuid, text) is
  'US-13.10/US-31.3: the ONE fail-closed capability predicate behind pool '
  'listing, claim, and the git-proxy read gate. Zero rows means the worker is '
  'offered nothing. US-43.1/US-44.1: the argument is a RUN KIND and is mapped '
  'through run_kind_capability, so a kind with no matrix column of its own '
  'still resolves to a grant a manager can actually give.';
