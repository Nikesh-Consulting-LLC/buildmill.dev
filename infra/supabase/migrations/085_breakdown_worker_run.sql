-- 085_breakdown_worker_run: story breakdown joins the worker pool (US-2.33).
--
-- Breakdown was the last AI drafting step still done by a synchronous LLM
-- call in the API (workflow.py /breakdown/propose). It now matches PRD
-- (US-3.21): a kind='breakdown' run is dispatched to the pool, a worker
-- claims it over MCP, reads the approved PRD + repo + guidelines +
-- learnings, and hands the split back with submit_stories — which
-- auto-creates the child stories in 'draft'. Like a prd run it carries no
-- repo/branch fields; unlike it, success creates child issues rather than
-- an artifact (handled in db.complete_run).

-- runs.kind gains 'breakdown'.
alter table public.runs drop constraint runs_kind_check;
alter table public.runs add constraint runs_kind_check
  check (kind in ('plan', 'code', 'prd', 'breakdown'));

-- worker_instructions.run_kind gains 'breakdown' (any per-project template
-- rows; the fallback is baked_worker_instruction below).
alter table public.worker_instructions
  drop constraint worker_instructions_run_kind_check;
alter table public.worker_instructions
  add constraint worker_instructions_run_kind_check
  check (run_kind in ('prd', 'plan', 'code', 'release', 'breakdown'));

-- baked_worker_instruction v3: adds the 'breakdown' expectation. prd/plan/
-- code text is carried verbatim from 066 (create-or-replace must not drop it).
create or replace function public.baked_worker_instruction(p_kind text)
returns text
language sql
immutable
as $$
  select case p_kind
    when 'prd' then
      'Write a product requirements document for this feature from the raw '
      || 'idea and context provided. Produce exactly these four markdown '
      || 'sections, in this order: ## Problem, ## Goals, ## Out of scope, '
      || '## Acceptance criteria. Be concrete and testable in the '
      || 'acceptance criteria; keep scope honest — anything doubtful goes '
      || 'to Out of scope. If this is a redraft, address the send-back '
      || 'feedback directly instead of starting over.'
    when 'breakdown' then
      'Break the approved PRD into engineering stories. Study the '
      || 'repository first over MCP (get_repo_tree, read_repo_file) and '
      || 'read the project guidelines and learnings, so the split fits the '
      || 'actual codebase. Produce self-contained stories, each with a '
      || 'title, a story body, and concrete acceptance criteria, ordered by '
      || 'dependency. Honor the breakdown mode and the manager''s '
      || 'instructions in the context: ''single'' means exactly one story '
      || 'covering the whole PRD; ''multiple'' means a detailed split. Hand '
      || 'the split back with submit_stories — the factory creates the '
      || 'child stories as drafts for the manager to curate.'
    when 'plan' then
      'Study the repository first, then produce a plan — not code. Read it '
      || 'over MCP with get_repo_tree and read_repo_file; no clone is '
      || 'needed. Do not modify any project file. Write an implementation '
      || 'plan (approach, files to touch, risks) and a test plan (how the '
      || 'change will be verified). Propose concrete test cases where '
      || 'useful. Honor the acceptance criteria and the PRD context when '
      || 'present; if this is a re-plan, address the send-back feedback.'
    when 'code' then
      'Implement the change honoring the approved implementation plan and '
      || 'the acceptance criteria. Follow the project guidelines and '
      || 'learnings. Keep the diff focused — no drive-by refactors. Hand '
      || 'back over MCP unless you have git tooling: get_workspace pins a '
      || 'base_sha, work on the extracted tree, submit_changeset declares '
      || 'that base_sha, then report_test_results against the run '
      || 'context''s test case ids. Git-capable workers may instead clone '
      || 'the factory remote, push the run''s branch, and submit_code_work. '
      || 'If this is a retry, address the rejection feedback directly.'
    else null
  end;
$$;

-- dispatch_breakdown: queue a breakdown run for a ready feature. Mirrors
-- dispatch_prd_draft's shape; guards are breakdown-specific (approved PRD
-- required, no existing children).
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
