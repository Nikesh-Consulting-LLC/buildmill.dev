-- 066_code_instruction_git_free_first: the baked code-run worker
-- instruction describes both hand-back transports, MCP-only first
-- (US-5.28). Template change only: existing projects' copied
-- worker_instructions rows keep their text (copy-at-pick semantics);
-- new projects, blank-content fallback, and "Reset to default" pick
-- this up. The run context renders the mechanics fresh either way.

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
