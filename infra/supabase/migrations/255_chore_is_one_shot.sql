-- 255_chore_is_one_shot (us-96.1): a chore dispatches straight to a code run.
--
-- Every work-item type rode the same pipe: plan run -> plan review -> code
-- run -> code review. For a chore the plan gate protects nothing; it doubles
-- the cost and the waiting. From here a chore is single-shot: dispatch
-- creates the code run, the one gate with teeth (code review) stays, and a
-- retry is always another code run. The state machine does not change —
-- planning/plan-review/planned simply never occur for a chore.
--
-- Five moves:
--   1. worker_instructions admits a 'chore' kind, with a baked single-shot
--      default, seeded on new projects and backfilled on existing ones.
--   2. instruction_kind_for(issue, run_kind) — the ONE mapping from an
--      issue's type to the instruction text its run reads. us-96.1 adds the
--      chore branch; us-96.2 (bug_rca/bug_fix) and us-96.3 (standalone_*)
--      extend the same function. build_issue_instructions resolves through
--      it, and the API's context-serve path calls it with the same arguments,
--      so the two cannot disagree.
--   3. dispatch_kind_for grows a type branch: a chore's inferred kind is
--      always 'code', legal from draft/ready/failed/needs-fixes, with no
--      approved-plan requirement; naming 'plan' on a chore is refused.
--   4. dispatch_issue stops attaching plan/test_plan context keys to a
--      chore's code run (absent, not null).
--   5. issue_hold_reason's every-sibling-plan-approved wait (switch 2, code
--      phase) stops counting chore siblings — they will never have one.

-- 1a ------------------------------------------------------------ the kind
alter table public.worker_instructions
  drop constraint if exists worker_instructions_run_kind_check;
alter table public.worker_instructions
  add constraint worker_instructions_run_kind_check
  check (run_kind in (
    'prd', 'plan', 'code', 'release', 'breakdown', 'test', 'deploy',
    'guidelines', 'elaborate', 'wireframe',
    'story_breakdown', 'test_case_elaborate', 'deploy_script_generate',
    'chore'
  ));

-- 1b ------------------------------------------- the baked default, restated
-- Full restatement of the live body (verified byte-identical on prod and dev
-- before this migration; the 187-style surgery is retired in favor of the
-- file being readable again), plus the new 'chore' case at the end.
create or replace function public.baked_worker_instruction(p_kind text)
returns text
language sql
immutable
as $function$
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
      || 'needed. The approved work is in the repo under docs/factory/. '
      || 'Read docs/factory/index.json for what exists and in what order, '
      || 'then the stories that precede yours in the same feature before '
      || 'designing — their approved plans and Outcome sections say what '
      || 'was decided and what actually shipped, not just what the code '
      || 'implies. Do not modify any project file. '
      || 'Write the implementation plan in EXACTLY four sections. '
      || '**What changes** — bullets naming the outcome: what a user or a '
      || 'caller can do afterward that they could not before. '
      || '**Surfaces touched** — the AREAS the change lands in, one line '
      || 'each, no justification: "the dispatch RPC", "the review page", '
      || '"the worker pool query". These are areas, NOT file paths. '
      || '**Risks** — what could go wrong, and what it would break. '
      || '**Dependencies** — what must be true, or must land first. '
      || 'Do NOT enumerate file paths anywhere in the plan. You are reading '
      || 'the repository through a straw; the agent that codes this holds '
      || 'the working tree and is better placed to choose files than you '
      || 'are. A plan that lists files buys an expensive crawl and hands '
      || 'downstream a decision made with less evidence. '
      || 'This plan does not bind the coding agent: it chooses the files, '
      || 'the structure and the tests, and may depart from your shape when '
      || 'the working tree says otherwise. Describe what must change, not '
      || 'how to type it. '
      || 'Also write a test plan — but read this carefully, because '
      || 'approving it CREATES ROWS: every case you write becomes a test '
      || 'case in the manager''s library for a person to walk by hand. '
      || 'Write THREE TO SIX acceptance-level cases in the ```json fence, '
      || 'each phrased as something a person can observe — "a hand-back is '
      || 'accepted when acceptance_criteria arrives as a single string" — '
      || 'not as an assertion about an internal function. Fewer is legal: '
      || 'a story with one observable outcome gets one case. Six is a '
      || 'ceiling on ambition, not a quota. '
      || 'Unit and integration tests are the CODING agent''s work, not '
      || 'yours: it has the working tree and can actually run them, and you '
      || 'have neither. Do not enumerate them. You may say what KIND of '
      || 'coverage the change deserves — a migration wants a rolled-back '
      || 'SQL test, a parser wants malformed input — without listing the '
      || 'tests themselves. '
      || 'Honor the acceptance criteria and the PRD context when '
      || 'present; if this is a re-plan, address the send-back feedback. '
      || 'Do not write exit criteria that require RUNNING a suite (e.g. '
      || '"pytest green", "npm test passes") — you cannot know whether the '
      || 'worker that picks up the code run has an environment to run it '
      || 'in. State the bar as tests authored and validate_submission '
      || 'clean, and leave execution to whoever can actually observe it. If a Wireframe section is present in your context, this story has already been drawn and reviewed: your Surfaces touched must be consistent with those screens, naming the same surfaces they show. If you believe the screen is wrong, say so under Risks — do not quietly design around it, and do not restate the wireframe as a fifth section. '
      || 'Narrate as you go: call report_progress with a short real note '
      || 'at meaningful boundaries — after claiming, when you start '
      || 'writing, when a major piece lands — so the manager can tell '
      || 'working from frozen. A note also extends your lease.'
    when 'code' then
      'Implement the change honoring the approved implementation plan and '
      || 'the acceptance criteria. The plan says WHAT changes, not which '
      || 'files to edit: choosing the files, the structure and the tests is '
      || 'yours, and departing from the plan''s shape is expected when the '
      || 'working tree says otherwise. Honour its intent, not its layout. '
      || 'Follow the project guidelines and '
      || 'learnings. The docs tree is already in your workspace — '
      || 'docs/factory/ is a local directory and needs no tool call. Read '
      || 'docs/factory/index.json for what exists and in what order, then '
      || 'the preceding stories in your feature before designing anything; '
      || 'their approved plans and Outcome sections carry the decisions you '
      || 'are extending. '
      || 'Keep the diff focused — no drive-by refactors. Hand '
      || 'back over MCP unless you have git tooling: get_workspace pins a '
      || 'base_sha, work on the extracted tree, submit_changeset declares '
      || 'that base_sha. Git-capable workers may instead clone the factory '
      || 'remote, push the run''s branch, and submit_code_work. '
      || 'On tests: writing them is always part of the work; RUNNING them '
      || 'depends on your environment. If you can execute the suite, do, '
      || 'and report_test_results against the run context''s test case '
      || 'ids. If you cannot, submit anyway and report nothing — never '
      || 'report a result you did not observe, and never stall the run '
      || 'waiting for an ability you do not have. Unreported cases stay '
      || 'unrun and the manager sees that honestly. Use blocked (with '
      || 'evidence) only for a case someone looked at and could not run. '
      || 'If this is a retry, address the rejection feedback directly. If a Wireframe section is present in your context, the rendered screen is already in your workspace under docs/wireframes/ — open it and build to it. Where the code has to depart from it, say so in your hand-back notes so the manager learns it at review rather than from a screenshot. '
      || 'Narrate as you go: call report_progress with a short real note '
      || 'at meaningful boundaries — after claiming, before a long write, '
      || 'when a major piece lands, before submitting — so the manager can '
      || 'tell working from frozen. A note also extends your lease.'
    when 'test' then
      'A staffed verification pass over a submitted code run''s branch. '
      || 'Check the branch out read-only through the factory remote (your '
      || 'token is the HTTP Basic password) — a test run never pushes. '
      || 'Apply the build configuration, run the project''s declared '
      || 'commands, and execute the manager''s test cases from the work '
      || 'context. Report per-case outcomes with report_test_results — '
      || 'passed, failed, or blocked (with evidence) — and ONLY what you '
      || 'actually observed. Execution is the work: if you cannot execute '
      || 'anything, release_work with a note saying why instead of '
      || 'completing empty. Fixes are not yours to make — failures flow to '
      || 'the manager''s gate, and the retry is a code run. Finish with '
      || 'submit_test_run and a short summary of what ran and where.'
    when 'release' then 'You are preparing ONE release: the one your claimed run names. Read what actually changed FIRST with get_release_changes — the commits, the changed files, and the work items in the range. Never infer a release''s contents from the current tree, and if the range comes back truncated, say so in the notes instead of writing around it. Then write two things. notes_summary: a few lines a manager reads at a glance, whose title carries the release version exactly as the factory computed it. notes_detail: what a reviewer actually needs — database and schema changes, migrations applied, modules affected, and anything operationally risky. The version is read from the release, never chosen by you. Then ship it: trigger_deployment sends the release''s PINNED commit to the project''s UAT deployment, get_deployment_run_status polls it, and get_deployment_health verifies it. Never claim an outcome you did not observe, and never submit a release whose deployment did not succeed — report the failure and stop. Finally, author regression test cases for the release as a whole: integration across the included work items, and anything the migrations imply. They are attached alongside the cases those work items already carry, for a human to run by hand. Finish with submit_release_run. If your work context says you are resuming, pick up from what is already done rather than redoing it. Nothing promotes to Production from this run — the manager''s UAT sign-off is what unlocks that.'
    when 'deploy' then
      'Execute and babysit ONE deployment: the one your claimed run names '
      || '— the trigger tools refuse any other. The server does the '
      || 'deploying; your job is trigger, observe, verify, report. '
      || 'trigger_deployment starts it; poll get_deployment_run_status '
      || 'until it finishes; then verify with get_deployment_health before '
      || 'declaring anything. Never claim an outcome you did not observe. '
      || 'Rollback happens only when the manager pre-authorized it at '
      || 'dispatch, only once, and only on failed health checks — '
      || 'otherwise report deployed-but-unhealthy and stop. Finish with '
      || 'submit_deploy_run and the honest verdict: deployed, '
      || 'deployed-unhealthy, or rolled-back.'
    when 'guidelines' then
      'Write this project''s guidelines from what is actually in the '
      || 'repository — not from convention, and not from what the existing '
      || 'guidelines already claim. READ FIRST, WRITE SECOND. Study the '
      || 'source over MCP (get_repo_tree, read_repo_file) or from the '
      || 'workspace: manifests and lockfiles for the real stack and its '
      || 'versions, the scripts that actually exist in package.json, '
      || 'Makefile, pyproject or CI for the commands, the test setup, and '
      || 'the CI workflows, container files and infra directories for how '
      || 'it ships. The work-item digest in your context is the delivery '
      || 'history — what was built, what broke, what was abandoned; it is '
      || 'where the footguns come from. '
      || 'Propose a section ONLY where the repository supports it: a '
      || 'single-package repo gets no monorepo notes, and a guess is worse '
      || 'than an omission because the next agent cannot tell them apart. '
      || 'Ground every command you write in a script that exists — never a '
      || 'plausible one. Where a section is already right, leave its text '
      || 'alone rather than rewriting it to sound different. '
      || 'ALWAYS propose the Deployment and Release section (section_key '
      || '''deployment''), even when the evidence is thin — say what you '
      || 'looked for and did not find, and name the file behind every '
      || 'claim you do make. It is prose describing how this project '
      || 'ships, not a script. '
      || 'Honor the scope and the focus note you were dispatched with: '
      || 'existing-sections-only means propose nothing that is not already '
      || 'there, EXCEPT the deployment section. '
      || 'Hand the whole pass back in ONE call to '
      || 'submit_guidelines_refresh — the manager reviews it as a single '
      || 'document and accepts it section by section. Nothing you write is '
      || 'applied automatically. Narrate as you go with report_progress so '
      || 'the manager can tell working from frozen; a note also extends '
      || 'your lease.'
    when 'elaborate' then
      'Rewrite ONE story so it is worth planning. It was written from a PRD '
      || 'by an agent that had never read this repository; you have the '
      || 'repository, and the plan run that follows you costs far more than '
      || 'you do. Read first: get_repo_tree and read_repo_file, plus the '
      || 'project guidelines and learnings in your context. '
      || 'Then propose a story body and acceptance criteria. Name REAL '
      || 'files, symbols, routes and tables — a plausible-sounding name is '
      || 'worse than none, because the next agent cannot tell them apart. '
      || 'Keep every acceptance criterion independently checkable: one '
      || 'observable outcome each, no compound criteria. '
      || 'PRESERVE the manager''s own wording wherever it is still right. '
      || 'You are sharpening a story, not restating it in your own voice, '
      || 'and a rewrite that only changes the phrasing wastes the gate. '
      || 'STAY INSIDE THIS STORY''S SLICE. The sibling stories are in your '
      || 'context so you can see the seams: do not absorb work that belongs '
      || 'to one of them, do not propose splitting this story in two, and '
      || 'do not rename or re-parent anything. Only the body and the '
      || 'acceptance criteria are yours to change. '
      || 'If the story is already fine, say so and propose nothing — that '
      || 'is a real answer and it costs the manager one glance. Anything '
      || 'you could not settle from the repository goes in open_questions '
      || 'rather than into a guess buried in the text. '
      || 'Finish with submit_elaboration. Nothing you write is applied: the '
      || 'manager reads your proposal beside the current text and decides.'
    when 'wireframe' then
      'Draw ONE story as a screen, before anyone plans it. What you hand '
      || 'back is read by a manager deciding whether this is the right '
      || 'screen, and then by the agent that codes it. '
      || 'READ FIRST. Use get_repo_tree and read_repo_file to find the '
      || 'app''s EXISTING screens and copy their shape: where the page '
      || 'title sits, where actions sit, what a list looks like, what the '
      || 'empty state says. A wireframe that invents a new layout for a '
      || 'screen that already has siblings is wrong even when it is pretty. '
      || 'DECLARE, DO NOT STYLE. Your output is a JSON declaration rendered '
      || 'by the project''s wireframe kit — no HTML, no CSS, no colours, no '
      || 'pixel sizes. The components are named for the app''s own: card, '
      || 'table, page-header, button, badge, status-badge, tabs, dialog, '
      || 'field, empty-state, toast, avatar, separator, skeleton, menu, '
      || 'row, grid, stack, text. Reading your wireframe should tell a '
      || 'coder which component to reach for. '
      || 'COVER THE STATES the story implies — populated, empty, loading, '
      || 'error. Declare them on the screen and the kit renders each one; '
      || 'a table needs no help (it becomes skeleton rows when loading and '
      || 'its own empty state when empty). Most bugs live in the states '
      || 'nobody drew. '
      || 'USE REAL COPY. Real labels, real column headings, the real empty '
      || 'sentence. Never lorem ipsum, never "Button 1". The words are half '
      || 'of what the manager is judging. '
      || 'ANNOTATE. Put "ac": <number> on the region that satisfies each '
      || 'acceptance criterion, so the review is a verification and not an '
      || 'opinion. '
      || 'IF THE STORY HAS NO SCREEN — a migration, a capability gate, a '
      || 'metering fix — hand back no_ui_surface with a reason naming what '
      || 'it changes instead. That is a real answer and the right one; do '
      || 'NOT invent a screen to avoid it. '
      || 'Stay inside this story''s slice: the sibling stories'' screens are '
      || 'in your context so a feature''s screens agree with each other, '
      || 'not so you can draw theirs. '
      || 'Finish with submit_wireframe. There is no approval gate — what you '
      || 'hand back is what the manager reads and what the repository gets, '
      || 'so hand back the screen you would defend.'
    when 'chore' then
      'This is a chore — single-shot work. No plan run came before this '
      || 'and none will: the item''s own title, body and acceptance '
      || 'criteria are the whole contract, so implement directly from '
      || 'them. Keep the diff small and strictly scoped — a chore never '
      || 'carries a refactor; if the work turns out bigger than a chore, '
      || 'say so in your hand-back notes instead of growing the diff. '
      || 'Follow the project guidelines and learnings. Hand back over MCP '
      || 'unless you have git tooling: get_workspace pins a base_sha, work '
      || 'on the extracted tree, submit_changeset declares that base_sha. '
      || 'Git-capable workers may instead clone the factory remote, push '
      || 'the run''s branch, and submit_code_work. There is no test plan '
      || 'on a chore, so your hand-back notes are the verification story: '
      || 'say plainly how a human confirms the change worked. If this is a '
      || 'retry, address the rejection feedback directly. Narrate as you '
      || 'go: call report_progress with a short real note at meaningful '
      || 'boundaries so the manager can tell working from frozen; a note '
      || 'also extends your lease.'
    else null
  end;
$function$;

-- 2 ------------------------------------------------- the type-aware mapping
-- The single place an issue's type turns a run kind into an instruction
-- kind. Both readers resolve through it: build_issue_instructions (the
-- dispatch-time seed and the preview) and the API's context-serve path
-- (db.get_worker_instruction). us-96.2 adds the bug branches, us-96.3 the
-- standalone-story pair — extend THIS function, never a call site.
create or replace function public.instruction_kind_for(p_issue uuid, p_run_kind text)
returns text
language sql
stable
as $function$
  select case
    when p_issue is null or p_run_kind not in ('plan', 'code') then p_run_kind
    else coalesce((
      select case
        when i.type = 'chore' and p_run_kind = 'code' then 'chore'
        else p_run_kind
      end
      from public.issues i
      where i.id = p_issue
    ), p_run_kind)
  end;
$function$;

comment on function public.instruction_kind_for(uuid, text) is
  'Which worker_instructions row a run of this kind on this issue reads '
  '(us-96.1). Identity for everything except the type-differentiated kinds; '
  'a chore''s code run reads ''chore''. us-96.2/96.3 extend the mapping.';

-- build_issue_instructions resolves through the mapping. Body carried from
-- 189 verbatim except the worker_instruction_for line.
create or replace function public.build_issue_instructions(p_issue uuid, p_kind text)
returns text
language plpgsql
stable
as $function$
declare
  v_issue public.issues%rowtype;
  v_text text;
  v_ac text;
  v_plan text;
begin
  select * into v_issue from public.issues where id = p_issue;
  if not found then
    return null;
  end if;

  v_text := '## Expectations — ' || p_kind || ' run' || E'\n\n'
    || coalesce(public.worker_instruction_for(
         v_issue.project_id,
         public.instruction_kind_for(p_issue, p_kind)), '');

  if v_issue.body is not null and length(trim(v_issue.body)) > 0 then
    v_text := v_text || E'\n\n## Story\n\n' || v_issue.body;
  end if;

  select string_agg('- ' || ac.value, E'\n')
  into v_ac
  from jsonb_array_elements_text(
    coalesce(v_issue.acceptance_criteria, '[]'::jsonb)
  ) ac;
  if v_ac is not null then
    v_text := v_text || E'\n\n## Acceptance criteria\n\n' || v_ac;
  end if;

  select a.content into v_plan
  from public.artifacts a
  where a.issue_id = p_issue and a.kind = 'plan' and a.status = 'approved'
  order by a.version desc limit 1;
  if v_plan is not null then
    v_text := v_text || E'\n\n## Approved plan\n\n' || v_plan;
  end if;

  return v_text;
end;
$function$;

-- 1c ------------------------------------------------------ seed + backfill
-- Body carried from 227, 'chore' added to the instruction-kind list.
create or replace function public.seed_worker_instructions()
returns trigger
language plpgsql
security definer
set search_path = public
as $function$
begin
  insert into public.worker_instructions (org_id, project_id, run_kind, content)
  select
    new.org_id, new.id, k.kind,
    coalesce(
      (
        select s.content from public.org_project_template_sections s
        where s.org_template_id = new.org_template_id
          and s.section_type = 'worker_instruction'
          and s.section_key = k.kind
      ),
      public.default_worker_instruction(k.kind),
      ''
    )
  from (values
    ('prd'), ('plan'), ('code'), ('release'), ('breakdown'), ('test'),
    ('deploy'), ('guidelines'), ('elaborate'), ('wireframe'), ('chore')
  ) as k(kind)
  on conflict (project_id, run_kind) do nothing;

  -- The two project-shaped thinking prompts, seeded from the template's
  -- 'prompt' sections when present (else left blank — resolve_prompt falls
  -- back to the global override / LLM_FUNCTIONS default).
  insert into public.worker_instructions (org_id, project_id, run_kind, content)
  select
    new.org_id, new.id, k.kind,
    coalesce(
      (
        select s.content from public.org_project_template_sections s
        where s.org_template_id = new.org_template_id
          and s.section_type = 'prompt'
          and s.section_key = k.kind
      ),
      ''
    )
  from (values ('test_case_elaborate'), ('deploy_script_generate')) as k(kind)
  on conflict (project_id, run_kind) do nothing;

  return new;
end;
$function$;

insert into public.worker_instructions (org_id, project_id, run_kind, content)
select p.org_id, p.id, 'chore', public.default_worker_instruction('chore')
from public.projects p
on conflict (project_id, run_kind) do nothing;

-- 3 ---------------------------------------------- dispatch_kind_for, typed
-- Body carried from 209 with the chore branch. A chore: never plans, codes
-- from draft/ready/failed/needs-fixes with no approved-plan requirement.
create or replace function public.dispatch_kind_for(p_issue uuid, p_kind text default null::text)
returns text
language plpgsql
stable
as $function$
declare
  v_status text;
  v_type text;
  v_has_approved_plan boolean;
  v_can_plan boolean;
  v_can_code boolean;
  v_kind text;
begin
  select status, type into v_status, v_type
  from public.issues where id = p_issue;
  if not found then
    raise exception 'issue not found';
  end if;

  select exists(
    select 1 from public.artifacts
    where issue_id = p_issue and kind = 'plan' and status = 'approved'
  ) into v_has_approved_plan;

  -- us-96.1: a chore has no planning phase — its one dispatchable kind is
  -- 'code', from any recoverable status, with no plan requirement.
  v_can_plan := v_type <> 'chore'
                and v_status in ('draft', 'ready', 'failed', 'needs-fixes', 'planned');
  v_can_code := case
    when v_type = 'chore'
      then v_status in ('draft', 'ready', 'failed', 'needs-fixes')
    else v_has_approved_plan
         and v_status in ('planned', 'needs-fixes', 'failed')
  end;

  if p_kind is null then
    if v_type = 'chore' then
      if v_can_code then
        v_kind := 'code';
      else
        raise exception 'issue is not dispatchable from status "%"', v_status;
      end if;
    elsif v_has_approved_plan and v_status in ('planned', 'needs-fixes', 'failed') then
      v_kind := 'code';
    elsif v_status in ('draft', 'ready', 'failed') then
      v_kind := 'plan';
    elsif v_status = 'needs-fixes' and not v_has_approved_plan then
      v_kind := 'plan';
    else
      raise exception 'issue is not dispatchable from status "%"', v_status;
    end if;
  elsif p_kind = 'plan' then
    if v_type = 'chore' then
      raise exception 'a chore has no planning phase — dispatch builds it';
    end if;
    if not v_can_plan then
      raise exception 'issue is not dispatchable for planning from status "%"', v_status;
    end if;
    v_kind := 'plan';
  elsif p_kind = 'code' then
    if v_type <> 'chore' and not v_has_approved_plan then
      raise exception 'code run requires an approved plan';
    end if;
    if not v_can_code then
      raise exception 'issue is not dispatchable for coding from status "%"', v_status;
    end if;
    v_kind := 'code';
  else
    raise exception 'unknown run kind "%" — expected "plan" or "code"', p_kind;
  end if;

  if v_type = 'feature' and v_kind = 'plan' then
    raise exception 'a feature is not planned directly — approve its PRD and break it into stories, then plan those';
  end if;

  if v_kind = 'code' and v_type <> 'chore' and not v_has_approved_plan then
    raise exception 'code run requires an approved plan';
  end if;

  return v_kind;
end;
$function$;

comment on function public.dispatch_kind_for(uuid, text) is
  'US-27.11 / US-57.13 / us-96.1: which run kind a Dispatch click means. A '
  'failed story with an approved plan infers "code"; a chore ALWAYS infers '
  '"code" (single-shot, no plan gate) and refuses a named "plan".';

-- 4 ------------------------------------------------- dispatch_issue, typed
-- Body carried from 235; one change: a chore''s code run gets no
-- plan/test_plan context keys — absent, not null (us-96.1 AC2).
create or replace function public.dispatch_issue(p_issue uuid, p_kind text default null)
returns uuid
language plpgsql
as $function$
declare
  v_issue public.issues%rowtype;
  v_project public.projects%rowtype;
  v_prev public.runs%rowtype;
  v_feedback text;
  v_context jsonb;
  v_run uuid;
  v_kind text;
  v_child_count int;
  v_prd_content text;
  v_plan_content text;
  v_test_plan_content text;
  v_pre_status text;
  v_prd_issue uuid;
  v_refusal text;
begin
  select * into v_issue from public.issues where id = p_issue for update;
  if not found then
    raise exception 'issue not found';
  end if;
  if v_issue.abandoned_at is not null then
    raise exception 'issue is abandoned';
  end if;

  select count(*) into v_child_count
  from public.issues
  where parent_id = p_issue and abandoned_at is null;
  if v_issue.type = 'feature' and v_child_count > 0 then
    raise exception 'feature with child stories is not dispatchable';
  end if;

  v_kind := public.dispatch_kind_for(p_issue, p_kind);

  select * into v_project from public.projects where id = v_issue.project_id;

  -- US-74.5: the feature-owns-the-build and sequential-only refusals now live
  -- in issue_dispatch_refusal so the UI can ask the same question without
  -- provoking the error. Same conditions, same wording.
  v_refusal := public.issue_dispatch_refusal(p_issue, v_kind);
  if v_refusal is not null then
    raise exception '%', v_refusal;
  end if;

  select * into v_prev
  from public.runs
  where issue_id = p_issue and kind = v_kind
  order by created_at desc
  limit 1;

  v_feedback := null;
  if v_prev.id is not null then
    if v_kind = 'code' then
      select a.comment into v_feedback
      from public.approvals a
      where a.subject_type = 'run'
        and a.subject_id = v_prev.id
        and a.gate = 'code-review'
        and a.decision = 'rejected'
      order by a.created_at desc
      limit 1;
    else
      select a.comment into v_feedback
      from public.approvals a
      where a.issue_id = p_issue
        and a.gate = 'plan'
        and a.decision = 'sent-back'
      order by a.created_at desc
      limit 1;
    end if;
  end if;

  v_prd_issue := coalesce(v_issue.parent_id, case when v_issue.type = 'feature' then v_issue.id end);

  v_context := jsonb_build_object(
    'title', v_issue.title,
    'type', v_issue.type,
    'story', v_issue.body,
    'body', v_issue.body,
    'acceptance_criteria', v_issue.acceptance_criteria,
    'repo_full_name', v_project.repo_full_name,
    'default_branch', v_project.default_branch,
    'run_kind', v_kind,
    'guidelines', public.assemble_project_guidelines(v_issue.project_id),
    'learnings', public.assemble_project_learnings(v_issue.project_id),
    'documents', coalesce((
      select jsonb_agg(jsonb_build_object(
        'id', d.id,
        'name', d.name,
        'mime_type', d.mime_type,
        'size_bytes', d.size_bytes,
        'attached_to', d.attached_to
      ) order by d.created_at)
      from public.documents d
      where (d.issue_id = p_issue and d.attached_to = 'work-item')
         or (v_prd_issue is not null
             and d.issue_id = v_prd_issue and d.attached_to = 'prd')
    ), '[]'::jsonb),
    'test_cases', coalesce((
      select jsonb_agg(jsonb_build_object(
        'id', t.id,
        'title', t.title,
        'steps', t.steps,
        'expected_result', t.expected_result
      ) order by t.created_at)
      from public.test_cases t
      where t.issue_id = p_issue and t.status = 'active'
    ), '[]'::jsonb)
  );

  if v_prd_issue is not null then
    select a.content into v_prd_content
    from public.artifacts a
    where a.issue_id = v_prd_issue and a.kind = 'prd' and a.status = 'approved'
    order by a.version desc limit 1;
    if v_prd_content is not null then
      v_context := v_context || jsonb_build_object('prd', v_prd_content);
    end if;
  end if;

  if v_kind = 'code' then
    -- us-96.1: a chore is single-shot — there is no plan to carry, and the
    -- keys stay absent rather than riding as JSON nulls.
    if v_issue.type <> 'chore' then
      select a.content into v_plan_content
      from public.artifacts a
      where a.issue_id = p_issue and a.kind = 'plan' and a.status = 'approved'
      order by a.version desc limit 1;
      select a.content into v_test_plan_content
      from public.artifacts a
      where a.issue_id = p_issue and a.kind = 'test_plan' and a.status = 'approved'
      order by a.version desc limit 1;
      v_context := v_context || jsonb_build_object(
        'plan', v_plan_content,
        'test_plan', v_test_plan_content
      );
    end if;
  else
    select a.content into v_plan_content
    from public.artifacts a
    where a.issue_id = p_issue and a.kind = 'plan'
    order by a.version desc limit 1;
    if v_plan_content is not null then
      v_context := v_context || jsonb_build_object('previous_plan', v_plan_content);
    end if;
  end if;

  if v_feedback is not null then
    v_context := v_context || jsonb_build_object(
      'feedback', v_feedback,
      'previous_branch', v_prev.branch_ref,
      'previous_pr_url', v_prev.pr_url
    );
  end if;

  perform public.seed_issue_instructions(p_issue, v_kind);

  v_pre_status := v_issue.status;

  insert into public.runs (org_id, issue_id, provider, status, kind, input_context, prev_issue_status)
  values (v_issue.org_id, p_issue, 'claude', 'queued', v_kind, v_context, v_pre_status)
  returning id into v_run;

  update public.issues set status = 'queued' where id = p_issue;

  insert into public.issue_events (org_id, issue_id, type, payload)
  values (
    v_issue.org_id,
    p_issue,
    case when v_kind = 'plan' then 'plan-dispatched' else 'dispatched' end,
    jsonb_build_object(
      'run_id', v_run,
      'kind', v_kind,
      'from_status', v_pre_status,
      'kind_chosen_by', case when p_kind is null then 'inferred' else 'manager' end
    )
  );

  return v_run;
end;
$function$;

-- 5 ------------------------------------- the hold rules skip chore plans
-- Body carried from 247 verbatim except one predicate: the switch-2
-- every-sibling-plan-approved wait no longer counts chore siblings — a
-- chore never has a plan artifact, so counting it wedges the feature
-- forever (us-96.1 AC6).
create or replace function public.issue_hold_reason(p_issue uuid, p_kind text)
returns text
language plpgsql
stable
as $function$
declare
  v_issue public.issues%rowtype;
  v_follow boolean;
  v_featone boolean;
  v_unit uuid;
  v_feature uuid;
  v_epic uuid;
  v_feat_no int;
  v_epic_no int;
  v_my_epic_no int;
  v_my_queued timestamptz;
  v_cnt int;
  v_blocker text;
begin
  if p_issue is null then
    return null;
  end if;
  select * into v_issue from public.issues where id = p_issue;
  if not found then
    return null;
  end if;

  -- US-43.5: a guidelines refresh is not delivery work; no rule reaches it.
  if p_kind = 'guidelines' then
    return null;
  end if;

  -- US-15.3: a story run is held while any non-abandoned sibling is still
  -- draft. US-44.1: elaborate/wireframe exempt (they fix that condition).
  if v_issue.parent_id is not null and p_kind not in ('elaborate', 'wireframe') then
    select count(*) into v_cnt
    from public.issues sib
    where sib.parent_id = v_issue.parent_id
      and sib.abandoned_at is null
      and sib.status = 'draft';
    if v_cnt > 0 then
      return format('waiting: %s sibling stor%s still being curated',
        v_cnt, case when v_cnt = 1 then 'y' else 'ies' end);
    end if;
  end if;

  select coalesce(follow_build_order, true), coalesce(route_feature_as_one, true)
    into v_follow, v_featone
  from public.projects where id = v_issue.project_id;

  -- My routing unit: the feature when switch 2 groups stories, else myself.
  v_unit := case
    when v_featone and v_issue.parent_id is not null then v_issue.parent_id
    else v_issue.id
  end;

  -- US-86.1, the law: one unit in progress, start to merge. Another unit
  -- anywhere between its first claim and its merge holds everything —
  -- including an approved plan parked awaiting the build, which is the
  -- manager's own gate to clear. 'failed' does NOT hold: a failed attempt
  -- ended its journey until the manager redispatches it.
  select
      coalesce(
        case when ep.number is not null and i.item_no is not null then
          case when i.type = 'feature' then 'FEAT-' || ep.number || '.' || i.item_no
               else (case i.type when 'bug' then 'BUG-' when 'chore' then 'CHORE-' else 'US-' end)
                    || ep.number || '.' || i.item_no
                    || coalesce('.' || i.sub_no, '')
          end
        end,
        i.title)
      || case i.status
           when 'plan-review' then ' is awaiting your plan approval'
           when 'planned' then ' holds an approved plan awaiting build'
           when 'in-review' then '''s PR is not merged yet'
           when 'needs-fixes' then '''s PR is not merged yet'
           else ' is being built'
         end
    into v_blocker
  from public.issues i
  left join public.epics ep on ep.id = i.epic_id
  where i.project_id = v_issue.project_id
    and i.abandoned_at is null
    and (case when v_featone and i.parent_id is not null then i.parent_id else i.id end) <> v_unit
    and i.status in ('planning', 'plan-review', 'planned', 'running',
                     'in-review', 'needs-fixes')
  order by i.updated_at asc
  limit 1;
  if v_blocker is not null then
    return 'waiting: ' || v_blocker;
  end if;

  -- Among queued units with nothing in progress, exactly one is offerable:
  -- the first in build order (switch 1 on) or in dispatch order (off).
  -- Compared only when I am queued myself — before dispatch there is no
  -- queue position to lose.
  if v_issue.status = 'queued' then
    if v_follow then
      select number into v_my_epic_no from public.epics where id = v_issue.epic_id;
      select coalesce(
               case when ep.number is not null and i.item_no is not null then
                 case when i.type = 'feature' then 'FEAT-' || ep.number || '.' || i.item_no
                      else (case i.type when 'bug' then 'BUG-' when 'chore' then 'CHORE-' else 'US-' end)
                           || ep.number || '.' || i.item_no
                           || coalesce('.' || i.sub_no, '')
                 end
               end,
               i.title)
        into v_blocker
      from public.issues i
      left join public.epics ep on ep.id = i.epic_id
      where i.project_id = v_issue.project_id
        and i.abandoned_at is null
        and i.status = 'queued'
        and (case when v_featone and i.parent_id is not null then i.parent_id else i.id end) <> v_unit
        and (coalesce(ep.number, 2147483647),
             coalesce(i.item_no, 2147483647),
             coalesce(i.sub_no, 2147483647),
             i.created_at)
          < (coalesce(v_my_epic_no, 2147483647),
             coalesce(v_issue.item_no, 2147483647),
             coalesce(v_issue.sub_no, 2147483647),
             v_issue.created_at)
      order by coalesce(ep.number, 2147483647),
               coalesce(i.item_no, 2147483647),
               coalesce(i.sub_no, 2147483647),
               i.created_at
      limit 1;
    else
      select min(r.created_at) into v_my_queued
      from public.runs r
      join public.issues ii on ii.id = r.issue_id
      where r.status = 'queued'
        and ii.project_id = v_issue.project_id
        and (case when v_featone and ii.parent_id is not null then ii.parent_id else ii.id end) = v_unit;

      if v_my_queued is not null then
        select coalesce(
                 case when ep.number is not null and i.item_no is not null then
                   case when i.type = 'feature' then 'FEAT-' || ep.number || '.' || i.item_no
                        else (case i.type when 'bug' then 'BUG-' when 'chore' then 'CHORE-' else 'US-' end)
                             || ep.number || '.' || i.item_no
                             || coalesce('.' || i.sub_no, '')
                   end
                 end,
                 i.title)
          into v_blocker
        from public.issues i
        left join public.epics ep on ep.id = i.epic_id
        join public.runs r on r.issue_id = i.id and r.status = 'queued'
        where i.project_id = v_issue.project_id
          and i.abandoned_at is null
          and (case when v_featone and i.parent_id is not null then i.parent_id else i.id end) <> v_unit
          and (r.created_at, i.id) < (v_my_queued, v_issue.id)
        order by r.created_at, i.id
        limit 1;
      end if;
    end if;
    if v_blocker is not null then
      return format('waiting: %s is ahead in the queue', v_blocker);
    end if;
  end if;

  -- Switch 1: hierarchy ordering — an earlier feature that isn't done yet
  -- goes first. Carried from 235, gated on the switch instead of the mode.
  if v_follow then
    if v_issue.type = 'feature' then
      v_feature := v_issue.id;
    elsif v_issue.parent_id is not null then
      v_feature := v_issue.parent_id;
    else
      v_feature := null;
    end if;

    if v_feature is not null then
      select epic_id, item_no into v_epic, v_feat_no
      from public.issues where id = v_feature;
      select number into v_epic_no from public.epics where id = v_epic;

      select coalesce(
               case when fe.number is not null and f.item_no is not null
                 then 'FEAT-' || fe.number || '.' || f.item_no || ' · ' || f.title
               end,
               f.title)
        into v_blocker
      from public.issues f
      join public.epics fe on fe.id = f.epic_id
      where f.project_id = v_issue.project_id
        and f.type = 'feature'
        and f.abandoned_at is null
        and f.status <> 'done'
        and f.id <> v_feature
        and (fe.number, f.item_no) < (v_epic_no, v_feat_no)
      order by fe.number, f.item_no
      limit 1;
      if v_blocker is not null then
        return format('waiting on an earlier feature to finish — %s', v_blocker);
      end if;
    end if;

    -- (d) trouble pauses healthy siblings — never the troubled story's own
    -- remediation (US-86.1 AC7, carried from 129/235).
    if v_issue.parent_id is not null and not public.issue_in_trouble(v_issue.id) then
      select format('%s-%s.%s.%s',
               case sib.type when 'bug' then 'BUG'
                             when 'chore' then 'CHORE'
                             else 'US' end,
               ep.number, sib.item_no, sib.sub_no)
        into v_blocker
      from public.issues sib
      join public.epics ep on ep.id = sib.epic_id
      where sib.parent_id = v_issue.parent_id
        and sib.abandoned_at is null
        and sib.id <> v_issue.id
        and public.issue_in_trouble(sib.id)
      order by sib.sub_no nulls last
      limit 1;
      if v_blocker is not null then
        return format('paused: story %s needs your attention', v_blocker);
      end if;
    end if;
  end if;

  -- Switch 2: the feature codes as one run, so every sibling's plan must be
  -- approved first. Carried from 235, gated on the switch. us-96.1: chore
  -- siblings are exempt from the count — a chore never has a plan artifact.
  if v_featone and p_kind = 'code' then
    if v_issue.type = 'feature' then
      v_feature := v_issue.id;
    else
      v_feature := v_issue.parent_id;
    end if;
    if v_feature is not null then
      select count(*) into v_cnt
      from public.issues sib
      where sib.parent_id = v_feature
        and sib.abandoned_at is null
        and sib.type <> 'chore'
        and not exists (
          select 1 from public.artifacts a
          where a.issue_id = sib.id
            and a.kind = 'plan' and a.status = 'approved'
        );
      if v_cnt > 0 then
        return format('waiting: %s sibling stor%s still need plan approval',
          v_cnt, case when v_cnt = 1 then 'y' else 'ies' end);
      end if;
    end if;
  end if;

  return null;
end;
$function$;

comment on function public.issue_hold_reason(uuid, text) is
  'Why a run of this kind for this work item would be held by the pool, or '
  'null if nothing holds it. US-86.1: one unit in progress per project, '
  'start to merge; switch 1 orders the queue, switch 2 sets the unit. '
  'us-96.1: chore siblings never gate a feature''s code phase on plan '
  'approval. run_hold_reason wraps this for an existing run.';
