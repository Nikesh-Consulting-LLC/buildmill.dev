-- 180_a_dead_run_settles_its_refresh: a refresh outlives the run that was
-- supposed to fill it.
--
-- `guideline_refreshes` is created `pending` at dispatch and only ever leaves
-- that state when the LAST recommendation in it is decided (173's trigger). A
-- run that fails, is cancelled, or is reaped therefore leaves the refresh
-- pending forever, with zero proposals, and two things follow:
--
--   * the one-open-refresh-per-project rule refuses every later attempt, so a
--     single failed run permanently disables the feature for that project;
--   * since the Things to Do card learned to distinguish running from ready,
--     a pending refresh with no proposals renders as "an agent is reading the
--     repository" — a spinner that never resolves.
--
-- Observed on production: a guidelines run failed at 17:58 with the runner
-- module error, and its refresh was still `pending` fifteen minutes later.
--
-- A TRIGGER on runs rather than a branch in complete_run, because a run
-- reaches a terminal state through more paths than one: the MCP submit,
-- `POST /worker/runs/{id}/submit` with an error, `cancel_run`, the
-- lease-expiry sweep and `reap_orphaned_provider_runs`. Only the database sees
-- all of them.
--
-- `succeeded` is deliberately NOT handled here: a successful run's refresh is
-- meant to stay pending — that is what "waiting on the manager" means, and
-- 173 closes it when the review is done.

create or replace function public.settle_refresh_on_dead_run()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  if new.kind <> 'guidelines' then
    return new;
  end if;
  -- Terminal and unsuccessful. `stopped` is included for the same reason
  -- complete_run still handles it: runs stopped before US-37.2 still exist.
  if new.status not in ('failed', 'cancelled', 'stopped') then
    return new;
  end if;
  if old.status = new.status then
    return new;
  end if;

  -- Only a refresh that never received anything. If proposals exist the run
  -- did its job and the manager still owns the decision, whatever happened to
  -- the run afterwards.
  update public.guideline_refreshes gr
     set status = 'decided', decided_at = now()
   where gr.run_id = new.id
     and gr.status = 'pending'
     and not exists (
       select 1 from public.guideline_recommendations x
        where x.refresh_id = gr.id
     );

  return new;
end;
$$;

drop trigger if exists settle_refresh_on_dead_run_trg on public.runs;
create trigger settle_refresh_on_dead_run_trg
  after update of status on public.runs
  for each row
  execute function public.settle_refresh_on_dead_run();

revoke execute on function public.settle_refresh_on_dead_run() from public, anon;

-- The one this was found on, and any other already stranded.
update public.guideline_refreshes gr
   set status = 'decided', decided_at = now()
  from public.runs r
 where r.id = gr.run_id
   and gr.status = 'pending'
   and r.status in ('failed', 'cancelled', 'stopped')
   and not exists (
     select 1 from public.guideline_recommendations x
      where x.refresh_id = gr.id
   );
