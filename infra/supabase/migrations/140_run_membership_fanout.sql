-- 140: approve and reject fan out across a run's membership (US-22.9).
--
-- A feature-level code run covers several stories through run_items, but
-- approve_run and reject_run both read runs.issue_id and move exactly one
-- issue. Left alone they would move the FEATURE and leave every story it
-- built sitting in `in-review` forever.
--
-- One commit means one decision: the manager cannot approve stories 1-3 and
-- reject 4-5. Approval merges every included story; rejection returns every
-- included story to needs-fixes with the feedback recorded on each, and the
-- retry is one combined run.
--
-- Single-story runs are unchanged: run_issue_ids falls back to runs.issue_id
-- when there is no membership, so both shapes take the same path.
--
-- NOTE ON release_records: us-22.9 was written expecting a row per story
-- here. us-21.7 dropped release_records entirely — a work item now ends at
-- Merged and a release is its own entity that snapshots what merged since the
-- last one. There is nothing to fan out, and the story has been corrected.

create or replace function public.approve_run(p_run uuid)
returns void
language plpgsql
security invoker
set search_path = public
as $$
declare
  v_run public.runs%rowtype;
  v_issue public.issues%rowtype;
  v_ids uuid[];
  v_id uuid;
  v_parent uuid;
  v_bad text;
begin
  select * into v_run from public.runs where id = p_run for update;
  if not found then
    raise exception 'run not found';
  end if;
  if v_run.kind <> 'code' then
    raise exception 'approve_run only applies to code runs';
  end if;

  select array_agg(issue_id order by ordinal)
    into v_ids
  from public.run_issue_ids(p_run);

  -- Every included story must be in review. Checking all of them BEFORE
  -- moving any keeps the batch atomic: a partially-merged batch would be a
  -- state no surface knows how to describe.
  select string_agg(i.title || ' (' || i.status || ')', ', ')
    into v_bad
  from public.issues i
  where i.id = any(v_ids) and i.status <> 'in-review';
  if v_bad is not null then
    raise exception 'issue is not in review (status "%")', v_bad;
  end if;

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

  -- The feature completes through the existing last-open-child rule rather
  -- than a second path. Checked once per distinct parent, after every story
  -- has moved — otherwise the first story in a batch would find its siblings
  -- still in review and never fire it.
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
end;
$$;

create or replace function public.reject_run(p_run uuid, p_comment text)
returns void
language plpgsql
security invoker
set search_path = public
as $$
declare
  v_run public.runs%rowtype;
  v_ids uuid[];
  v_id uuid;
  v_bad text;
begin
  if p_comment is null or length(trim(p_comment)) = 0 then
    raise exception 'a comment is required to reject';
  end if;

  select * into v_run from public.runs where id = p_run for update;
  if not found then
    raise exception 'run not found';
  end if;

  select array_agg(issue_id order by ordinal)
    into v_ids
  from public.run_issue_ids(p_run);

  select string_agg(i.title || ' (' || i.status || ')', ', ')
    into v_bad
  from public.issues i
  where i.id = any(v_ids) and i.status <> 'in-review';
  if v_bad is not null then
    raise exception 'issue is not in review (status "%")', v_bad;
  end if;

  -- The whole run goes back, with the feedback on every story it covered.
  -- There is one commit, so there is one decision — `story` mode is the
  -- answer for a manager who wants per-story gates.
  foreach v_id in array v_ids loop
    insert into public.approvals
      (org_id, issue_id, gate, subject_type, subject_id, decision, comment, actor)
    values
      (v_run.org_id, v_id, 'code-review', 'run', p_run, 'rejected',
       p_comment, auth.uid());

    update public.issues set status = 'needs-fixes' where id = v_id;

    insert into public.issue_events (org_id, issue_id, type, payload)
    values (v_run.org_id, v_id, 'rejected',
            jsonb_build_object('run_id', p_run, 'comment', p_comment));
  end loop;
end;
$$;
