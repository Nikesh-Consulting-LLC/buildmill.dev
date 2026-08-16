-- 273_the_release_instruction_describes_the_job (us-101.6): the Release
-- instruction stops describing a job that does not exist.
--
-- 269 rewrote this default eight days ago and left its tooling untouched, so
-- the text a manager can edit still says:
--
--   * "Finish with submit_release_run" — not an MCP tool at all. The name
--     survives only inside prompt strings. The hand-back is
--     submit_release_notes.
--   * "trigger_deployment / get_deployment_run_status / get_deployment_health"
--     — these EXIST, but each resolves a `runs` row of kind `deploy`, and a
--     release prep lives in release_prep_runs and has no runs row. They are
--     structurally uncallable from the job this text describes. Worse, the
--     text tells the agent to deploy and verify, and an agent that believes
--     it deployed writes that it did — into notes a manager signs off.
--   * "your context carries them" about the Agent Instructions — nothing on
--     the release-prep path delivered them. us-101.6 makes that sentence
--     true (the claim now carries the document, the project's Release
--     instruction and the notes vocabulary), which is what lets this text
--     finally be worth editing.
--
-- What it does NOT restate: the section names and block types. Those are
-- generated from the renderer by release_notes.vocabulary_brief() and reach
-- the agent on the claim, so the instruction and the page cannot drift.
--
-- Same shape as 269: splice the one `when` branch, hash every OTHER kind
-- before and after and roll back if any moved, then backfill only rows that
-- still hold the OLD default verbatim. A row a manager has edited is left
-- exactly as it is.

do $migration$
declare
  def text;
  old_release text := public.baked_worker_instruction('release');
  new_release text :=
    'You are preparing ONE release: the one your claimed run names. Read what '
    || 'actually changed FIRST with get_release_changes — the commits, the '
    || 'changed files, the migrations and modules in the range, and the work '
    || 'items it includes WITH their acceptance criteria and the test cases '
    || 'they already carry. Never infer a release''s contents from the current '
    || 'tree, and if the range comes back truncated, repeat what `note` says '
    || 'instead of writing around it. '
    || 'Then decide what to call it: read the versioning rules in the '
    || 'project''s Agent Instructions, which arrive with this job. If they '
    || 'define a scheme, propose a version that follows it in proposed_version '
    || 'and say why in version_rationale — the factory checks that it is free '
    || 'and could be a git tag, and falls back to its own YYYY.MM.DD.N if it '
    || 'is not. If they say nothing about versioning, propose nothing. The '
    || 'manager may override the proposal at cut, and after that the version '
    || 'is fixed — you never change one. '
    || 'Then write the release for the person who has to test it. '
    || 'notes_summary: a few lines a manager reads at a glance, whose first '
    || 'line carries the version. notes_detail: what a reviewer actually needs '
    || '— database and schema changes, migrations applied, modules affected, '
    || 'and anything operationally risky. notes_doc: the page itself, in the '
    || 'vocabulary your brief lists — a standfirst saying how to work through '
    || 'the release, a note per section, and prose or callouts for what does '
    || 'not fit. '
    || 'Then write the checks. Every included work item must be accounted '
    || 'for: a case you wrote naming it in `story`, a case it already carries '
    || '(get_release_changes shows you those — do not write them again), or '
    || 'its id in `uncovered` saying you left it deliberately. A case is a '
    || 'title, `steps` saying what to DO, and an `expected_result` saying what '
    || 'to SEE — a tester who has never read the story must be able to run it, '
    || 'and a manager must be able to confirm it without asking you what you '
    || 'meant. Put each one in a section so the list reads in the order it '
    || 'should be worked, and mark `critical` the two or three the test suite '
    || 'cannot make for anybody. A title with nothing behind it is refused. '
    || 'NEVER describe a deployment, its duration, or a test-suite result. '
    || 'None of them exist while you are writing — the UAT deploy is fired by '
    || 'your own hand-back, after it succeeds, and the release page fills '
    || 'those facts in itself once they are real. Claiming an outcome you did '
    || 'not observe is the one failure here a manager cannot catch by reading. '
    || 'Finish with submit_release_notes. If your work context says you are '
    || 'resuming, pick up from what is already done rather than redoing it. '
    || 'Nothing promotes to Production from this run — the manager''s UAT '
    || 'sign-off is what unlocks that.';
  guarded text[] := array[
    'prd', 'plan', 'code', 'breakdown', 'test', 'deploy', 'guidelines',
    'elaborate', 'wireframe', 'chore', 'bug_rca', 'bug_fix',
    'standalone_plan', 'standalone_code', 'merge'
  ];
  before_hash text;
  after_hash text;
  n_projects int;
  n_tpl int;
  n_org_tpl int;
begin
  if old_release is null then
    raise exception 'baked default for release not found';
  end if;
  if position('submit_release_run' in old_release) = 0 then
    raise notice 'already applied (or hand-edited) — skipping';
    return;
  end if;

  select p.prosrc into def
  from pg_proc p join pg_namespace n on n.oid = p.pronamespace
  where n.nspname = 'public' and p.proname = 'baked_worker_instruction';
  if def is null then
    raise exception 'baked_worker_instruction not found';
  end if;

  select md5(string_agg(k || ':' || coalesce(public.baked_worker_instruction(k), ''),
                        '|' order by k))
    into before_hash
  from unnest(guarded) as k;

  def := regexp_replace(
    def,
    'when ''release'' then.*?(?=\s+when ''|\s+else)',
    'when ''release'' then ' || quote_literal(new_release)
  );

  execute
    'create or replace function public.baked_worker_instruction(p_kind text) '
    || 'returns text language sql immutable as $fn$'
    || def
    || '$fn$';

  select md5(string_agg(k || ':' || coalesce(public.baked_worker_instruction(k), ''),
                        '|' order by k))
    into after_hash
  from unnest(guarded) as k;

  if before_hash is distinct from after_hash then
    raise exception
      'a guarded kind changed (% -> %) — the splice reached too far; '
      'rolling back', before_hash, after_hash;
  end if;
  if public.baked_worker_instruction('release') is distinct from new_release then
    raise exception 'release default did not take — rolling back';
  end if;

  -- Backfill: only rows still holding the OLD default verbatim. Without
  -- this the migration changes the factory default and nothing else, because
  -- every project already carries a seeded row — and the projects that most
  -- need the fix are the ones that have been running longest.
  update public.worker_instructions
     set content = new_release
   where run_kind = 'release' and content = old_release;
  get diagnostics n_projects = row_count;

  update public.project_template_sections
     set content = new_release
   where section_type = 'worker_instruction' and section_key = 'release'
     and content = old_release;
  get diagnostics n_tpl = row_count;

  update public.org_project_template_sections
     set content = new_release
   where section_type = 'worker_instruction' and section_key = 'release'
     and content = old_release;
  get diagnostics n_org_tpl = row_count;

  raise notice 'release default: % project rows, % platform template, % org template',
    n_projects, n_tpl, n_org_tpl;
end
$migration$;
