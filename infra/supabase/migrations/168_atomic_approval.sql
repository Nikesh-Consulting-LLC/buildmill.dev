-- 168: US-40.1 — an approval that is refused has merged nothing.
--
-- On 2026-07-28 a manager approved a six-story feature build. The endpoint
-- squash-merged PR #12 into `main` and then refused to record the approval,
-- leaving the code shipped and the system of record unaware of it.
--
-- The cause was two checks that had to agree and did not. `reviews.py` guards
-- before the irreversible merge by reading ONE issue — `runs.issue_id`, which
-- for a feature batch is the feature, and the feature was `in-review`.
-- `approve_run` then reads `run_issue_ids(p_run)` — the member STORIES — and
-- those were `planned`, so it raised. The guard was written when a run meant
-- one issue; us-27.1 taught the RPC the wider set and left the guard narrow.
--
-- The fix is not a wider copy of the check. Two checks that must agree are one
-- check called twice, so the predicate moves into a function both callers use.
-- A third status or a wider membership now changes one place.
--
-- `approve_run_precheck` is deliberately read-only and deliberately returns
-- the reason rather than raising: the API needs to inspect it BEFORE deciding
-- to touch GitHub, and an exception is a poor carrier for "may I proceed".
-- `approve_run` keeps raising — it is the transactional authority and must not
-- become advisory just because something asked politely first.

create or replace function public.approve_run_precheck(p_run uuid)
returns text
language plpgsql
stable
as $$
declare
  v_run public.runs%rowtype;
  v_bad text;
begin
  select * into v_run from public.runs where id = p_run;
  if not found then
    return 'run not found';
  end if;
  if v_run.kind <> 'code' then
    return 'approve_run only applies to code runs';
  end if;

  -- The one predicate. `approve_run` raises from this same shape; keeping the
  -- wording identical means the manager sees one message whichever layer
  -- refuses.
  select string_agg(i.title || ' (' || i.status || ')', ', ')
    into v_bad
  from public.issues i
  where i.id in (select issue_id from public.run_issue_ids(p_run))
    and i.status <> 'in-review';

  if v_bad is not null then
    return format('issue is not in review (status "%s")', v_bad);
  end if;

  return null;
end;
$$;

comment on function public.approve_run_precheck(uuid) is
  'US-40.1: the refusal reason approve_run would raise, or null when it would '
  'succeed. Read-only, so the API can ask before merging a pull request it '
  'cannot un-merge. Never a substitute for approve_run''s own check — the '
  'transaction is still the authority.';


-- The split-brain marker -----------------------------------------------------
--
-- With the precheck in place the ordering hole is closed for every refusal the
-- predicate can see. It cannot close a RACE — a status moving between the
-- precheck and the RPC — or a transient failure after the merge succeeded.
-- Those are rare and no longer silent: the run records that its PR is merged
-- while its approval is not, so the state is visible in the app instead of
-- only on GitHub.
--
-- Added BEFORE approve_run is redefined, because that body assigns to it.
alter table public.runs
  add column if not exists merged_unapproved_at timestamptz;

comment on column public.runs.merged_unapproved_at is
  'US-40.1: set when the pull request merged but approve_run then failed. The '
  'code is on the default branch and the factory has not recorded it. Cleared '
  'by a successful approve_run. A run carrying this offers "Finish approval", '
  'which records the approval WITHOUT calling GitHub again — the merge already '
  'happened and cannot happen twice.';


-- approve_run, rewritten to raise FROM the precheck rather than beside it.
-- Everything after the check is byte-for-byte what it was.
create or replace function public.approve_run(p_run uuid)
returns void
language plpgsql
as $$
declare
  v_run public.runs%rowtype;
  v_issue public.issues%rowtype;
  v_ids uuid[];
  v_id uuid;
  v_parent uuid;
  v_refusal text;
begin
  select * into v_run from public.runs where id = p_run for update;
  if not found then
    raise exception 'run not found';
  end if;
  if v_run.kind <> 'code' then
    raise exception 'approve_run only applies to code runs';
  end if;

  -- US-40.1: the same predicate the API consulted before merging. Re-read
  -- inside the transaction and under the row lock, because between the
  -- precheck and here a status can move.
  v_refusal := public.approve_run_precheck(p_run);
  if v_refusal is not null then
    raise exception '%', v_refusal;
  end if;

  select array_agg(issue_id order by ordinal)
    into v_ids
  from public.run_issue_ids(p_run);

  foreach v_id in array v_ids loop
    select * into v_issue from public.issues where id = v_id for update;

    insert into public.approvals
      (org_id, issue_id, gate, subject_type, subject_id, decision, actor)
    values
      (v_run.org_id, v_issue.id, 'code-review', 'run', p_run, 'approved', auth.uid());

    update public.issues set status = 'merged' where id = v_issue.id;

    insert into public.issue_events (org_id, issue_id, type, payload)
    values
      (v_run.org_id, v_issue.id, 'approved', jsonb_build_object('run_id', p_run)),
      (v_run.org_id, v_issue.id, 'merged',
       jsonb_build_object('run_id', p_run, 'pr_url', v_run.pr_url));
  end loop;

  for v_parent in
    select distinct i.parent_id
    from public.issues i
    where i.id = any(v_ids) and i.parent_id is not null
  loop
    if not exists (
      select 1 from public.issues c
      where c.parent_id = v_parent
        and c.abandoned_at is null
        and c.status not in ('merged', 'done')
    ) then
      update public.issues set status = 'done' where id = v_parent;
      insert into public.issue_events (org_id, issue_id, type, payload)
      values (
        v_run.org_id, v_parent, 'feature-completed',
        jsonb_build_object('trigger_run_id', p_run)
      );
    end if;
  end loop;

  -- US-40.1: the approval landed, so the run is no longer half-applied.
  update public.runs set merged_unapproved_at = null where id = p_run;
end;
$$;


revoke execute on function public.approve_run_precheck(uuid) from public;
revoke execute on function public.approve_run_precheck(uuid) from anon;
grant execute on function public.approve_run_precheck(uuid) to authenticated, service_role;

notify pgrst, 'reload schema';
