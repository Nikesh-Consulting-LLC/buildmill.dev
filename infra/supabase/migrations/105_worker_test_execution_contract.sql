-- 105_worker_test_execution_contract (US-11.5): tell workers, in the baked
-- instructions, who is expected to RUN the tests.
--
-- !! BROKEN AS SHIPPED — see 106_restore_instruction_kinds_after_105.sql.
-- This migration rebuilt baked_worker_instruction() from the 066 body,
-- which predates the 'breakdown' (085) and 'release' (077/095) cases, so
-- replacing the function dropped them and new project inserts began
-- violating worker_instructions.content NOT NULL. 106 restores all five
-- cases with this migration's contract text intact. Kept here as applied,
-- rather than rewritten, so the history matches the database.
--
-- The gap this closes: a plan is executed by whichever worker claims the
-- code run, and workers differ in capability. A supervisor runner (Phase
-- 10) has a controlled shell and can genuinely run a suite. A bare MCP
-- client has no checkout, no interpreter, and no package manager — it can
-- author test files but cannot execute them. Nothing said so, and nothing
-- stopped a plan from making "pytest green" a hard exit criterion.
--
-- Observed 2026-07-20: a code run deadlocked twice. The agent wrote all 45
-- files, could not run the suite, and refused to report results it had not
-- observed — correct discipline, but it left the run stalled until its
-- lease expired, with no honest way forward. The run only completed
-- because a human ran the suites out-of-band, which is not a path a real
-- worker has.
--
-- Two instruction texts change:
--   'code' — say plainly that running the suite depends on the worker's
--            environment, that unobserved results must never be reported,
--            and that a worker which cannot run tests should submit anyway
--            rather than stall.
--   'plan' — tell the planning agent not to write exit criteria that
--            require executing a suite, since it cannot know which kind of
--            worker will pick up the code run.
--
-- Copy-at-pick semantics (066): projects hold their own worker_instructions
-- rows, so changing the baked default does not reach them. Rows that no
-- person has ever edited (updated_by is null) are refreshed to the new
-- default; rows a manager has edited are left alone, because their
-- customization outranks this.
--
-- `updated_by is null` is used rather than matching the previous baked
-- string: some rows still carry a *pre-066* default, so exact-string
-- matching would silently skip exactly the stalest instructions. The column
-- records human edits, which is the distinction that actually matters.
--
-- Managers who have customized these texts will not receive the contract
-- here. get_work_context renders the hand-back mechanics fresh on every run
-- regardless of the stored instruction, and US-11.5 puts the contract there
-- too — so a customized project still gets told, at run time.

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
    when 'plan' then
      'Study the repository first, then produce a plan — not code. Read it '
      || 'over MCP with get_repo_tree and read_repo_file; no clone is '
      || 'needed. Do not modify any project file. Write an implementation '
      || 'plan (approach, files to touch, risks) and a test plan (how the '
      || 'change will be verified). Propose concrete test cases where '
      || 'useful. Honor the acceptance criteria and the PRD context when '
      || 'present; if this is a re-plan, address the send-back feedback. '
      || 'Do not write exit criteria that require RUNNING a suite (e.g. '
      || '"pytest green", "npm test passes") — you cannot know whether the '
      || 'worker that picks up the code run has an environment to run it '
      || 'in. State the bar as tests authored and validate_submission '
      || 'clean, and leave execution to whoever can actually observe it.'
    when 'code' then
      'Implement the change honoring the approved implementation plan and '
      || 'the acceptance criteria. Follow the project guidelines and '
      || 'learnings. Keep the diff focused — no drive-by refactors. Hand '
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
      || 'If this is a retry, address the rejection feedback directly.'
    else null
  end;
$$;

-- Refresh every copy no person has touched.
update public.worker_instructions
set content = public.baked_worker_instruction(run_kind)
where run_kind in ('plan', 'code')
  and updated_by is null
  and content <> public.baked_worker_instruction(run_kind);
