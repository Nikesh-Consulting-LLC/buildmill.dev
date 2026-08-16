-- 278_mark_issue_fixed (US-107.1): a work item can be completed without going
-- through the whole cycle, and the report it came from closes with it.
--
-- Not every defect earns plan → code → review → merge. Some are fixed in a
-- change already in flight, or turn out to be one line the manager just makes.
-- Until now the only way to clear one was **Abandon**, which says the opposite
-- of what happened: abandoned means "we decided not to do this", and the whole
-- point here is that it *was* done.
--
-- One transaction rather than three client writes, for the same reason
-- promote_app_issue is (183): the issue, its originating report and its parent
-- feature must agree, and a half-applied version of this leaves a completed bug
-- whose report is still sitting in the inbox asking to be triaged.

create or replace function public.mark_issue_fixed(p_issue uuid)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_issue public.issues%rowtype;
  v_principal uuid;
  v_report uuid;
  v_parent uuid;
begin
  select * into v_issue from public.issues where id = p_issue for update;
  if v_issue.id is null then
    raise exception 'work item not found';
  end if;
  if not public.is_org_member(v_issue.org_id) then
    raise exception 'not authorized';
  end if;

  -- A feature is never fixed by hand. It completes when its last story does —
  -- the rollup at the bottom of this function is what does that — and marking
  -- one directly would strand its open stories under a completed parent.
  if v_issue.type = 'feature' then
    raise exception
      'a feature completes when its last story does — mark the stories instead';
  end if;

  if v_issue.status in ('merged', 'done') then
    raise exception 'this work item is already complete';
  end if;

  -- The same rule Abandon already applies. A worker is mid-flight; completing
  -- the item underneath them discards work that is still being written, and
  -- the hand-back would then land on a finished item.
  if v_issue.status in ('queued', 'running') then
    raise exception
      'a run is queued or running — stop it before marking this fixed';
  end if;

  -- Abandoned and fixed are different claims, and the manager should not be
  -- able to make both at once without noticing. Restore, then mark.
  if v_issue.abandoned_at is not null then
    raise exception 'this work item is abandoned — restore it before marking it fixed';
  end if;

  select id into v_principal
  from public.principals where auth_user_id = (select auth.uid());

  -- `done` rather than `merged`: nothing was merged. It is the status the
  -- factory already uses for "complete, with no PR of its own" (033/168), so
  -- every existing consumer — TERMINAL_STATUSES, the hub's default filter, the
  -- feature rollup — counts it correctly without being taught a new word.
  update public.issues set status = 'done' where id = p_issue;

  insert into public.issue_events (org_id, issue_id, type, payload)
  values (
    v_issue.org_id, p_issue, 'marked-fixed',
    jsonb_build_object('from_status', v_issue.status, 'by', v_principal)
  );

  -- The report this bug was promoted from closes with it, using US-105.1's
  -- `fixed`. Terminal there too, and deliberately so: `fixed` sits outside
  -- app_issues_open_fingerprint_key, so the same crash arriving later opens a
  -- fresh report counting from one instead of reviving this one.
  update public.app_issues
  set status = 'fixed', triaged_by = v_principal, triaged_at = now()
  where promoted_issue_id = p_issue and status <> 'fixed'
  returning id into v_report;

  -- Mirrors approve_run (168) exactly. Without it, marking the last story of a
  -- feature fixed would leave that feature open forever — the only other place
  -- that closes a parent is the approval path this item never travels.
  if v_issue.parent_id is not null and not exists (
    select 1 from public.issues c
    where c.parent_id = v_issue.parent_id
      and c.abandoned_at is null
      and c.status not in ('merged', 'done')
  ) then
    update public.issues set status = 'done' where id = v_issue.parent_id;
    insert into public.issue_events (org_id, issue_id, type, payload)
    values (
      v_issue.org_id, v_issue.parent_id, 'feature-completed',
      jsonb_build_object('trigger_issue_id', p_issue, 'via', 'marked-fixed')
    );
    v_parent := v_issue.parent_id;
  end if;

  return jsonb_build_object(
    'issue_id', p_issue,
    'report_id', v_report,
    'feature_completed', v_parent
  );
end;
$$;

revoke execute on function public.mark_issue_fixed(uuid) from public, anon;
grant execute on function public.mark_issue_fixed(uuid) to authenticated;
