-- 173_guideline_refresh_settles: US-43.3 — a refresh closes itself.
--
-- The manager decides a bundle one section at a time, and there is no
-- "finish" button: when the last pending recommendation in a refresh is
-- decided, the refresh is decided and its chore is done.
--
-- A TRIGGER rather than a wrapper RPC, deliberately. The alternative is a
-- decide_guideline_refresh_section(...) that calls the 069 RPC and then
-- settles the parent — which means two write paths into project_guidelines
-- (one for ad-hoc us-5.32 rows, one for bundled ones), two things to keep in
-- step, and an "accept all remaining" loop that has to remember which one to
-- call. As a trigger, `decide_guideline_recommendation` stays the single,
-- unchanged, security-invoker write path, and settling is a consequence of
-- deciding rather than a second call that can be forgotten.
--
-- SECURITY DEFINER because the manager deciding a recommendation has no
-- insert/update policy on guideline_refreshes (171 grants none — refreshes
-- are created by the API's service connection). The definer body writes only
-- the parent of the row that just changed, so it cannot be steered.

create or replace function public.settle_guideline_refresh()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  v_issue uuid;
begin
  -- Only a bundled row that just left `pending` can close anything.
  if new.refresh_id is null or new.status = 'pending'
     or old.status <> 'pending' then
    return new;
  end if;

  if exists (
    select 1 from public.guideline_recommendations
    where refresh_id = new.refresh_id and status = 'pending'
  ) then
    return new;
  end if;

  update public.guideline_refreshes
     set status = 'decided', decided_at = now()
   where id = new.refresh_id and status = 'pending'
  returning issue_id into v_issue;

  -- The chore is done when the review is done, not when the agent handed
  -- back. `returning` is null when the refresh was already decided (a
  -- concurrent decide won the race), and then there is nothing to close.
  if v_issue is not null then
    update public.issues
       set status = 'done', updated_at = now()
     where id = v_issue and status <> 'done' and abandoned_at is null;
  end if;

  return new;
end;
$$;

drop trigger if exists settle_guideline_refresh_trg
  on public.guideline_recommendations;
create trigger settle_guideline_refresh_trg
  after update of status on public.guideline_recommendations
  for each row
  execute function public.settle_guideline_refresh();

revoke execute on function public.settle_guideline_refresh() from public, anon;
