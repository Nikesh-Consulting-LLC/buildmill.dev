-- 106_restore_instruction_kinds_after_105: repair baked_worker_instruction.
--
-- 105 (US-11.5) added the test-execution contract to the 'plan' and 'code'
-- texts, but built its `create or replace` on the 066 body — which predates
-- both the 'breakdown' case (085) and the 'release' case (077, restored by
-- 095). Replacing the function with that body silently dropped them, so
-- baked_worker_instruction('breakdown') and ('release') fell through to
-- `else null`. seed_worker_instructions() seeds every kind and
-- worker_instructions.content is NOT NULL, so EVERY new project insert
-- raised NotNullViolation — the same failure 095 was written to fix, and
-- caught here by the apps/api *_sql.py suite.
--
-- This restores all five cases with the 105 contract text intact. The
-- lesson, now twice learned: never rebuild this function from an older
-- migration's body — always start from the current definition.

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
    when 'release' then
      'Reference material for shipping to UAT or Production — not a run you '
      || 'are dispatched for. Versions are system-computed as '
      || 'V<epic>.<release-seq>: the major is the current epic number, the '
      || 'minor a per-epic release counter (V1.1, V1.2, then V2.1 once the '
      || 'epic rolls). The factory mints and git-tags the version at the '
      || 'release cut — never hand-pick one. When preparing a cut, write '
      || 'release notes that read as a changelog: user-facing changes '
      || 'first, then fixes and internal changes, listing the included work '
      || 'items by their epic-scoped ids. Ship to UAT first, record the QA '
      || 'sign-off, then promote the same version to Production — promotion '
      || 'never re-versions.'
    else null
  end;
$$;

-- Safety backfill, mirroring 095: any project left without a row for a kind
-- while the function was returning NULL (idempotent).
insert into public.worker_instructions (org_id, project_id, run_kind, content)
select p.org_id, p.id, k.kind, public.default_worker_instruction(k.kind)
from public.projects p
cross join (values ('prd'), ('breakdown'), ('plan'), ('code'), ('release')) as k(kind)
on conflict (project_id, run_kind) do nothing;
