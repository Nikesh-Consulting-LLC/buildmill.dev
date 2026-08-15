-- 269_the_refresh_and_release_defaults_speak_files (us-100.5 AC4, us-100.6):
-- two baked instruction defaults rewritten for what the runs now do.
--
--   guidelines  — the refresh run proposes WHOLE FILES (the Agent
--                 Instructions document and per-task .buildmill files), not
--                 catalog sections; the old text told an agent to name a
--                 section_key and "ALWAYS propose the Deployment section",
--                 neither of which exists any more.
--   release     — us-100.6 let a project's Agent Instructions define its
--                 versioning; the old text said "the version is read from
--                 the release, never chosen by you", which is now only true
--                 AFTER the cut. The agent proposes, with reasoning.
--
-- Same shape as 261: splice the two `when` branches inside
-- baked_worker_instruction's case, hash every OTHER kind before and after and
-- roll back if any moved. Then backfill: every project row and every template
-- section that still holds the OLD default verbatim (never edited — on prod
-- that is 10/10 projects for both kinds) takes the new one. A row a manager
-- has touched is left exactly as it is.

do $migration$
declare
  def text;
  old_guidelines text := public.baked_worker_instruction('guidelines');
  old_release text := public.baked_worker_instruction('release');
  new_guidelines text :=
    'Study this repository and propose better instructions for it — READ FIRST, '
    || 'WRITE SECOND. Your context carries the project''s Agent Instructions (the '
    || 'body of AGENTS.md — the conventions every agent reads first) and every '
    || 'per-task instruction file under .buildmill/ exactly as the factory holds '
    || 'them; those are what you are proposing against. Do not judge them by the '
    || 'copies in the workspace: those are generated FROM the factory''s text, so '
    || 'reading them back tells you nothing about whether they are any good. Study '
    || 'the source over MCP (get_repo_tree, read_repo_file) or from the workspace: '
    || 'manifests and lockfiles for the real stack and its versions, the scripts '
    || 'that actually exist in package.json, Makefile, pyproject or CI for the '
    || 'commands, the test setup, and the CI workflows, container files and infra '
    || 'directories for how it ships. The work-item digest in your context is the '
    || 'delivery history — what was built, what broke, what was abandoned; it is '
    || 'where the footguns come from. Then propose whole files: the full '
    || 'replacement text of the Agent Instructions, and of any per-task file that '
    || 'would steer its runs better for THIS project — a Code.md that names this '
    || 'repo''s build and test commands, a Test.md that says how its suites are '
    || 'split, a Release_Prep.md that states its versioning rule. Only where the '
    || 'repository supports it: a single-package repo gets no monorepo notes, and '
    || 'a guess is worse than an omission because the next agent cannot tell them '
    || 'apart. Ground every command you write in a script that exists — never a '
    || 'plausible one. Where a file is already right, leave it out rather than '
    || 'rewriting it to sound different; a proposal identical to the current file '
    || 'is refused. Honor the scope and the focus note you were dispatched with: '
    || 'document-only means propose the Agent Instructions and nothing else. Hand '
    || 'the whole pass back in ONE call to submit_guidelines_refresh — one entry '
    || 'per file with its full text and a rationale saying what is wrong today and '
    || 'why yours is better. The manager reads the rationale and the diff per '
    || 'file, and accepts or rejects the pass whole. Nothing you write is applied '
    || 'automatically, and nothing reaches the repository until the manager '
    || 'publishes. Narrate as you go with report_progress so the manager can tell '
    || 'working from frozen; a note also extends your lease.';
  new_release text :=
    'You are preparing ONE release: the one your claimed run names. Read what '
    || 'actually changed FIRST with get_release_changes — the commits, the changed '
    || 'files, and the work items in the range. Never infer a release''s contents '
    || 'from the current tree, and if the range comes back truncated, say so in '
    || 'the notes instead of writing around it. Then decide what to call it: read '
    || 'the versioning rules in the project''s Agent Instructions (your context '
    || 'carries them). If they define a scheme, propose a version that follows it '
    || 'in proposed_version and say why in version_rationale — the factory checks '
    || 'that it is free and could be a git tag, and falls back to its own '
    || 'YYYY.MM.DD.N if it is not. If they say nothing about versioning, propose '
    || 'nothing and the factory''s date-based version stands. The manager may '
    || 'override the proposal at cut, and after that the version is fixed — you '
    || 'never change one. Then write two things. notes_summary: a few lines a '
    || 'manager reads at a glance, whose title carries the release version. '
    || 'notes_detail: what a reviewer actually needs — database and schema '
    || 'changes, migrations applied, modules affected, and anything operationally '
    || 'risky. Then ship it: trigger_deployment sends the release''s PINNED commit '
    || 'to the project''s UAT deployment, get_deployment_run_status polls it, and '
    || 'get_deployment_health verifies it. Never claim an outcome you did not '
    || 'observe, and never submit a release whose deployment did not succeed — '
    || 'report the failure and stop. Finally, author regression test cases for '
    || 'the release as a whole: integration across the included work items, and '
    || 'anything the migrations imply. They are attached alongside the cases '
    || 'those work items already carry, for a human to run by hand. Finish with '
    || 'submit_release_run. If your work context says you are resuming, pick up '
    || 'from what is already done rather than redoing it. Nothing promotes to '
    || 'Production from this run — the manager''s UAT sign-off is what unlocks '
    || 'that.';
  guarded text[] := array[
    'prd', 'plan', 'code', 'breakdown', 'test', 'deploy',
    'elaborate', 'wireframe', 'chore', 'bug_rca', 'bug_fix',
    'standalone_plan', 'standalone_code', 'merge'
  ];
  before_hash text;
  after_hash text;
  n_projects int;
  n_tpl int;
  n_org_tpl int;
begin
  if old_guidelines is null or old_release is null then
    raise exception 'baked defaults for guidelines/release not found';
  end if;
  if position('submit_guidelines_refresh — one entry' in old_guidelines) > 0
     and position('proposed_version' in old_release) > 0 then
    raise notice 'already applied — skipping';
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

  -- Replace each branch's body: from `when '<kind>' then` up to (not
  -- including) the next `when '` or the `else`. Non-greedy, and `.` spans
  -- newlines in Postgres AREs by default. quote_literal doubles the
  -- apostrophes so the spliced text is a valid SQL literal.
  def := regexp_replace(
    def,
    'when ''guidelines'' then.*?(?=\s+when ''|\s+else)',
    'when ''guidelines'' then ' || quote_literal(new_guidelines)
  );
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
  if public.baked_worker_instruction('guidelines') is distinct from new_guidelines then
    raise exception 'guidelines default did not take — rolling back';
  end if;
  if public.baked_worker_instruction('release') is distinct from new_release then
    raise exception 'release default did not take — rolling back';
  end if;

  -- Backfill: only rows still holding the OLD default verbatim.
  update public.worker_instructions
     set content = new_guidelines
   where run_kind = 'guidelines' and content = old_guidelines;
  get diagnostics n_projects = row_count;
  update public.worker_instructions
     set content = new_release
   where run_kind = 'release' and content = old_release;
  raise notice 'project rows updated: guidelines %, release +', n_projects;

  update public.project_template_sections
     set content = new_guidelines
   where section_type = 'worker_instruction' and section_key = 'guidelines'
     and content = old_guidelines;
  update public.project_template_sections
     set content = new_release
   where section_type = 'worker_instruction' and section_key = 'release'
     and content = old_release;
  get diagnostics n_tpl = row_count;

  update public.org_project_template_sections
     set content = new_guidelines
   where section_type = 'worker_instruction' and section_key = 'guidelines'
     and content = old_guidelines;
  update public.org_project_template_sections
     set content = new_release
   where section_type = 'worker_instruction' and section_key = 'release'
     and content = old_release;
  get diagnostics n_org_tpl = row_count;
  raise notice 'template sections updated (release): platform %, org %', n_tpl, n_org_tpl;
end
$migration$;
