-- 119_breakdown_dispatch_dedupe: a feature can only be broken down once (US-15.9).
--
-- Observed on the Demo feature ab828516-… : two `breakdown` runs were created
-- six seconds apart, both claimed by the same worker, and BOTH ran to
-- completion — leaving 14 draft children instead of 7 (two overlapping splits
-- of the same PRD). Root cause: dispatch_breakdown only guarded against
-- existing *children*, but children aren't created until a breakdown run
-- *completes* (submit_stories → complete_run). Two dispatches seconds apart
-- therefore both saw "no children yet" and both queued a run. The `for update`
-- lock on the issue row serialised them but couldn't help — there was nothing
-- about the run itself to check.
--
-- Fix, two layers:
--   1. dispatch_breakdown refuses when a breakdown run for the issue is already
--      queued, running, or succeeded (below).
--   2. claim_run refuses to start a breakdown run once one has already
--      succeeded for the same issue (db.py, belt-and-suspenders for a duplicate
--      that somehow still exists).

create or replace function public.dispatch_breakdown(p_issue uuid)
returns uuid
language plpgsql
as $$
declare
  v_issue public.issues%rowtype;
  v_prd public.artifacts%rowtype;
  v_children int;
  v_context jsonb;
  v_run uuid;
begin
  select * into v_issue from public.issues where id = p_issue for update;
  if not found then
    raise exception 'issue not found';
  end if;
  if v_issue.abandoned_at is not null then
    raise exception 'issue is abandoned';
  end if;
  if v_issue.type <> 'feature' then
    raise exception 'only a feature can be broken into stories';
  end if;
  if v_issue.status <> 'ready' then
    raise exception 'only a ready feature can be broken into stories';
  end if;

  select * into v_prd
  from public.artifacts
  where issue_id = p_issue and kind = 'prd' and status = 'approved'
  order by version desc limit 1;
  if v_prd.id is null then
    raise exception 'approved PRD required';
  end if;

  -- US-15.9: a breakdown already queued, running, or succeeded means this
  -- feature is being (or has been) split — a second run would double the
  -- children. This is the real guard; the children check below stays as a
  -- second signal for the succeeded case. The issue row is locked FOR UPDATE
  -- above, so a concurrent dispatch blocks here until the first commits and
  -- then sees the queued run.
  perform 1 from public.runs
  where issue_id = p_issue and kind = 'breakdown'
    and status in ('queued', 'running', 'succeeded');
  if found then
    raise exception 'a breakdown run is already in progress or complete for this feature';
  end if;

  select count(*) into v_children
  from public.issues where parent_id = p_issue;
  if v_children > 0 then
    raise exception 'feature already has children — use Add story instead';
  end if;

  v_context := jsonb_build_object(
    'title', v_issue.title,
    'type', v_issue.type,
    'story', v_issue.body,
    'body', v_issue.body,
    'run_kind', 'breakdown',
    'prd', v_prd.content,
    'breakdown_mode', coalesce(v_issue.breakdown_mode, 'automatic'),
    'breakdown_instructions', v_issue.breakdown_instructions,
    'guidelines', public.assemble_project_guidelines(v_issue.project_id),
    'learnings', public.assemble_project_learnings(v_issue.project_id)
  );

  perform public.seed_issue_instructions(p_issue, 'breakdown');

  insert into public.runs (org_id, issue_id, provider, status, kind, input_context)
  values (v_issue.org_id, p_issue, 'claude', 'queued', 'breakdown', v_context)
  returning id into v_run;

  insert into public.issue_events (org_id, issue_id, type, payload)
  values (v_issue.org_id, p_issue, 'breakdown-dispatched',
          jsonb_build_object('run_id', v_run));

  return v_run;
end;
$$;
