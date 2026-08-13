-- US-13.4: approved work lands in the repo, owned by the app.
--
-- 1) Opt-in flag: writing requirement text into a customer repo is a
--    per-project policy call, off by default.
-- 2) baked_worker_instruction v-next: the plan and code texts tell the
--    worker to read the docs tree's index and the preceding stories
--    before designing. Built from the CURRENT definition (106) with all
--    five kinds carried verbatim — never from an older migration's body
--    (the 095/105/106 lesson).

alter table public.projects
  add column if not exists docs_tree_enabled boolean not null default false;

comment on column public.projects.docs_tree_enabled is
  'US-13.4: when true, the factory writes approved PRDs, stories and '
  'plans into the repo under docs/factory/ on each approval. The app '
  'owns that tree; outside edits are overwritten.';

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
      || 'needed. If the repo carries docs/factory/INDEX.md, read the '
      || 'index and the stories that precede yours in the same feature '
      || 'before designing — the decisions your predecessors made are '
      || 'recorded there, not just implied by their code. '
      || 'Do not modify any project file. Write an implementation '
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
      || 'learnings. If the repo carries docs/factory/INDEX.md, read the '
      || 'index and the preceding stories in your feature before designing '
      || 'anything — earlier decisions live there. '
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

-- Refresh unedited per-project copies of the two changed kinds so existing
-- projects pick up the docs-tree pointer (manager-edited rows are theirs).
update public.worker_instructions wi
set content = public.default_worker_instruction(wi.run_kind)
where wi.run_kind in ('plan', 'code')
  and wi.updated_by is null;
