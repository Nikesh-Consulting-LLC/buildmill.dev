-- 046_force_delete_issues: force-delete work items stuck in queued/running
-- (US-2.26 follow-up). The guard trigger from 031 keeps blocking plain
-- deletes; the force_delete_issues RPC below is the only path that bypasses
-- it, so a force delete is always a deliberate, confirmed user action.

-- Guard learns a transaction-local escape hatch set only by the RPC.
create or replace function public.guard_issue_removal()
returns trigger
language plpgsql
as $$
begin
  if TG_OP = 'DELETE' then
    if coalesce(current_setting('app.force_delete_issues', true), '') = 'on' then
      return old;
    end if;
    if old.status in ('queued', 'running') then
      raise exception 'Cannot delete an issue that is queued or running.';
    end if;
    return old;
  end if;

  if new.abandoned_at is not null
     and old.abandoned_at is null
     and new.status in ('queued', 'running') then
    raise exception 'Cannot abandon an issue that is queued or running.';
  end if;
  return new;
end;
$$;

-- Force-delete issues regardless of queued/running status. security definer
-- (RLS doesn't apply inside), so org membership is checked explicitly for
-- every requested id before anything is deleted. FK cascades remove events,
-- runs, reviews, approvals, and worker claims; linked test cases and
-- documents are detached (issue_id set null), not deleted. An in-flight
-- run's hand-back is discarded — this does not signal the worker.
create or replace function public.force_delete_issues(p_issue_ids uuid[])
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
  v_count integer;
begin
  if exists (
    select 1
    from public.issues i
    where i.id = any(p_issue_ids)
      and not public.is_org_member(i.org_id)
  ) then
    raise exception 'Not authorized to delete one or more work items.';
  end if;

  -- Transaction-local: resets when this call's transaction ends.
  perform set_config('app.force_delete_issues', 'on', true);
  delete from public.issues where id = any(p_issue_ids);
  get diagnostics v_count = row_count;
  return v_count;
end;
$$;

revoke execute on function public.force_delete_issues(uuid[]) from public, anon;
grant execute on function public.force_delete_issues(uuid[]) to authenticated;
