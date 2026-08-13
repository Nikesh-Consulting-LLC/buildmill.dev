-- 095_restore_release_worker_instruction: fix NULL release default.
--
-- 077 added a 'release' case to baked_worker_instruction(). 085
-- (breakdown_worker_run) redefined the function to add the 'breakdown' case but
-- dropped 'release', so default_worker_instruction('release') fell through to
-- `else null`. Because seed_worker_instructions() seeds all four kinds and
-- worker_instructions.content is NOT NULL, EVERY new project insert then raised
-- NotNullViolation — blocking project creation on the live DB and cascading the
-- apps/api *_sql.py suite. This restores the 'release' case, keeping the current
-- (085) prd/breakdown/plan/code text intact. Not related to Phase 9.
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

-- Safety backfill: any project missing its release instruction row (idempotent).
insert into public.worker_instructions (org_id, project_id, run_kind, content)
select p.org_id, p.id, 'release', public.default_worker_instruction('release')
from public.projects p
on conflict (project_id, run_kind) do nothing;
