-- 126_auto_approve_gates: auto-approve PRD / plan / code (US-17.4).
--
-- When a project's auto_approve_{prd,plan,code} switch is on, the factory
-- clears that gate the instant its run is submitted, through the same DB
-- effects a manual approval produces — but attributed to the project setting,
-- not a person. approvals.actor was `not null references auth.users`; make it
-- nullable and add auto_approved, so an automated decision is stored honestly
-- (actor null, auto_approved true) and is distinguishable in the log (US-17.5).

alter table public.approvals alter column actor drop not null;
alter table public.approvals
  add column if not exists auto_approved boolean not null default false;

comment on column public.approvals.auto_approved is
  'US-17.4: true when the project auto-approve setting cleared this gate (no '
  'human). actor is null for these.';

-- PRD: approve the draft, feature -> ready, then auto-dispatch the breakdown.
-- Returns the breakdown run id (or null if it could not be dispatched).
create or replace function public.auto_approve_prd(p_issue uuid)
returns uuid
language plpgsql
as $$
declare
  v_issue public.issues%rowtype;
  v_art public.artifacts%rowtype;
  v_run uuid;
begin
  select * into v_issue from public.issues where id = p_issue for update;
  if not found then raise exception 'issue not found'; end if;
  if v_issue.status <> 'prd-review' then
    raise exception 'issue is not in prd-review';
  end if;
  select * into v_art from public.artifacts
    where issue_id = p_issue and kind = 'prd' and status = 'draft'
    order by version desc limit 1;
  if v_art.id is null then raise exception 'no draft PRD to approve'; end if;

  update public.artifacts set status = 'approved' where id = v_art.id;
  insert into public.approvals
    (org_id, issue_id, gate, subject_type, subject_id, decision, actor, auto_approved)
  values
    (v_issue.org_id, p_issue, 'prd', 'artifact', v_art.id, 'approved', null, true);
  update public.issues set status = 'ready' where id = p_issue;
  insert into public.issue_events (org_id, issue_id, type, payload)
  values (v_issue.org_id, p_issue, 'prd-approved',
    jsonb_build_object('artifact_id', v_art.id, 'version', v_art.version, 'auto', true));

  begin
    v_run := public.dispatch_breakdown(p_issue);
  exception when others then
    v_run := null;  -- e.g. already broken down — don't fail the approval
  end;
  return v_run;
end;
$$;

-- Plan: approve the draft plan + test_plan, story -> planned, then auto-dispatch
-- the code run (test-case materialisation is done in Python by the caller).
-- Returns the code run id (or null).
create or replace function public.auto_approve_plan(p_issue uuid)
returns uuid
language plpgsql
as $$
declare
  v_issue public.issues%rowtype;
  a record;
  v_run uuid;
begin
  select * into v_issue from public.issues where id = p_issue for update;
  if not found then raise exception 'issue not found'; end if;
  if v_issue.status <> 'plan-review' then
    raise exception 'issue is not in plan-review';
  end if;
  if not exists (
    select 1 from public.artifacts
    where issue_id = p_issue and kind = 'plan' and status = 'draft'
  ) then
    raise exception 'no draft plan to approve';
  end if;

  for a in
    select id from public.artifacts
    where issue_id = p_issue and status = 'draft' and kind in ('plan', 'test_plan')
  loop
    update public.artifacts set status = 'approved' where id = a.id;
    insert into public.approvals
      (org_id, issue_id, gate, subject_type, subject_id, decision, actor, auto_approved)
    values
      (v_issue.org_id, p_issue, 'plan', 'artifact', a.id, 'approved', null, true);
  end loop;

  update public.issues set status = 'planned' where id = p_issue;
  insert into public.issue_events (org_id, issue_id, type, payload)
  values (v_issue.org_id, p_issue, 'plan-approved', jsonb_build_object('auto', true));

  begin
    v_run := public.dispatch_issue(p_issue);  -- creates the code run (held by mode if needed)
  exception when others then
    v_run := null;
  end;
  return v_run;
end;
$$;

-- Code: identical effects to approve_run (merge already done by the caller via
-- GitHub), but attributed to the auto-approve setting.
create or replace function public.auto_approve_code(p_run uuid)
returns void
language plpgsql
as $$
declare
  v_run public.runs%rowtype;
  v_issue public.issues%rowtype;
  v_record_id uuid;
begin
  select * into v_run from public.runs where id = p_run for update;
  if not found then raise exception 'run not found'; end if;
  if v_run.kind <> 'code' then raise exception 'auto_approve_code only applies to code runs'; end if;
  select * into v_issue from public.issues where id = v_run.issue_id for update;
  if v_issue.status <> 'in-review' then
    raise exception 'issue is not in review (status "%")', v_issue.status;
  end if;

  insert into public.approvals
    (org_id, issue_id, gate, subject_type, subject_id, decision, actor, auto_approved)
  values
    (v_run.org_id, v_issue.id, 'code-review', 'run', p_run, 'approved', null, true);

  update public.issues set status = 'merged' where id = v_issue.id;

  insert into public.release_records (org_id, issue_id, run_id, merge_commit_sha)
  values (v_run.org_id, v_issue.id, p_run, v_run.merge_commit_sha)
  on conflict (issue_id) do update
    set run_id = excluded.run_id,
        merge_commit_sha = coalesce(excluded.merge_commit_sha, release_records.merge_commit_sha)
  returning id into v_record_id;

  insert into public.issue_events (org_id, issue_id, type, payload)
  values
    (v_run.org_id, v_issue.id, 'approved', jsonb_build_object('run_id', p_run, 'auto', true)),
    (v_run.org_id, v_issue.id, 'merged',
     jsonb_build_object('run_id', p_run, 'pr_url', v_run.pr_url,
       'release_record_id', v_record_id, 'auto', true));

  -- Auto-complete parent feature when all children are merged/done (same as approve_run).
  if v_issue.parent_id is not null then
    if not exists (
      select 1 from public.issues c
      where c.parent_id = v_issue.parent_id
        and c.abandoned_at is null
        and c.status not in ('merged', 'done')
    ) then
      update public.issues set status = 'done' where id = v_issue.parent_id;
      insert into public.issue_events (org_id, issue_id, type, payload)
      values (v_run.org_id, v_issue.parent_id, 'feature-completed',
        jsonb_build_object('trigger_issue_id', v_issue.id, 'auto', true));
    end if;
  end if;
end;
$$;
