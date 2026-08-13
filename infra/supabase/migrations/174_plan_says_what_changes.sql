-- 174_plan_says_what_changes: US-45.1.
--
-- The plan instruction has always asked for "files to touch". Honouring that
-- means crawling the tree over MCP one read_repo_file at a time, and Phase 38
-- measured what it costs: a plan run averages 1.4M input tokens to produce a
-- 22k-token document — 23.4M input against 351k output across 22 metered runs,
-- a 67:1 ratio. The document is not the cost; the reading behind one of its
-- sections is.
--
-- It is also decided at the point of LEAST evidence. A plan run has no
-- workspace and no shell; a code run gets the extracted tree and reads the
-- repository again anyway, because it must. So the plan now says WHAT changes
-- and leaves HOW to the agent holding the workspace.
--
-- "Surfaces touched" is kept, coarse — areas, not paths. It is what preserves
-- the manager's cheapest intervention (a plan-gate send-back costs a comment;
-- a code-review rejection costs a whole run) and what catches two stories in a
-- batch rewriting the same function before they meet at the merge.
--
-- SURGERY OVER THE LIVE BODY, with 171's drift guard. baked_worker_instruction
-- has been rebuilt across 057, 066, 077, 085, 095, 105, 106, 108, 110, 112,
-- 114, 131, 133, 136 and 171, and 171's header records that it is NOT 114's
-- body any more. This reads what is actually there, refuses if the segment is
-- not what it expects, and replaces only the `plan` branch plus one sentence
-- in `code`. Every other kind is carried forward by construction.

do $migration$
declare
  def text;
  plan_old text;
  plan_new text := $branch$    when 'plan' then
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
      || 'Also write a test plan (how the change will be verified). Propose '
      || 'concrete test cases where useful. '
      || 'Honor the acceptance criteria and the PRD context when '
      || 'present; if this is a re-plan, address the send-back feedback. '
      || 'Do not write exit criteria that require RUNNING a suite (e.g. '
      || '"pytest green", "npm test passes") — you cannot know whether the '
      || 'worker that picks up the code run has an environment to run it '
      || 'in. State the bar as tests authored and validate_submission '
      || 'clean, and leave execution to whoever can actually observe it. '
      || 'Narrate as you go: call report_progress with a short real note '
      || 'at meaningful boundaries — after claiming, when you start '
      || 'writing, when a major piece lands — so the manager can tell '
      || 'working from frozen. A note also extends your lease.'
$branch$;
  -- The matching half, from the code side. Without it this change does not
  -- land: the code instruction says "honoring the approved implementation
  -- plan" and us-13.5 inlines that plan as the contract, so a THINNER
  -- contract read as strictly as a thick one produces a timid code run, not
  -- a free one.
  code_old text := $q$'Implement the change honoring the approved implementation plan and '
      || 'the acceptance criteria. $q$;
  code_new text := $q$'Implement the change honoring the approved implementation plan and '
      || 'the acceptance criteria. The plan says WHAT changes, not which '
      || 'files to edit: choosing the files, the structure and the tests is '
      || 'yours, and departing from the plan''s shape is expected when the '
      || 'working tree says otherwise. Honour its intent, not its layout. '
      || '$q$;
begin
  select prosrc into def
  from pg_proc p
  join pg_namespace n on n.oid = p.pronamespace
  where n.nspname = 'public' and p.proname = 'baked_worker_instruction';

  if def is null then
    raise exception 'baked_worker_instruction not found';
  end if;

  -- The whole plan branch, from its `when` to the next one.
  plan_old := substring(
    def from position('    when ''plan'' then' in def)
        for position('    when ''code'' then' in def)
            - position('    when ''plan'' then' in def)
  );
  if plan_old is null or plan_old = '' then
    raise exception
      'the plan branch is not where 174 expects it — '
      'baked_worker_instruction has drifted; re-derive this edit from its '
      'current definition rather than replacing it wholesale';
  end if;
  if position('files to touch' in plan_old) = 0 then
    raise notice 'the plan branch no longer asks for files to touch; '
      'assuming 174 has already been applied and leaving it alone';
    return;
  end if;
  if position(code_old in def) = 0 then
    raise exception
      'the code branch opening sentence is not where 174 expects it — '
      'baked_worker_instruction has drifted';
  end if;

  def := replace(def, plan_old, plan_new);
  def := replace(def, code_old, code_new);

  execute
    'create or replace function public.baked_worker_instruction(p_kind text) '
    || 'returns text language sql immutable as $fn$' || def || '$fn$';
end
$migration$;

-- Projects that never customised these instructions get the new text; a row a
-- manager has edited (updated_by is not null) is left exactly as they wrote
-- it. That per-project override (us-5.14) is the escape hatch for a project
-- that wants the old detailed plan back.
update public.worker_instructions
set content = public.baked_worker_instruction(run_kind)
where run_kind in ('plan', 'code') and updated_by is null;
