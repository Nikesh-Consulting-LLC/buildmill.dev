-- 175_test_plan_gives_direction: US-45.2.
--
-- The test plan is not a document. Approving it CREATES ROWS —
-- `_materialize_test_plan` inserts one `test_cases` row per parsed case with
-- source = 'agent' (workflow.py). Those rows are the manager's test library
-- and what a coding agent reports against with report_test_results.
--
-- So the question is not how detailed the prose should be. It is who should be
-- writing this project's test cases. Today it is an agent that CANNOT RUN
-- ANYTHING — a plan run has no workspace and no shell — inventing unit-shaped
-- assertions about code that does not exist yet, one story before the agent
-- that will have both. The code instruction already says the opposite,
-- correctly: "writing them is always part of the work; RUNNING them depends on
-- your environment."
--
-- The plan now says WHAT MUST BE TRUE; the coding agent writes the tests that
-- prove it.
--
-- The ```json fence STAYS. A prose-only test plan parses to zero cases and
-- trips validate_plan's existing finding on every run. That finding is right;
-- the fence is how the manager's UAT checklist gets built. Direction changes
-- what goes IN the cases, not the format that carries them.
--
-- Re-derived from the LIVE body, not from 174's file: two migrations editing
-- one function is only safe if each reads what is actually there. Same drift
-- guard — raise rather than overwrite.

do $migration$
declare
  def text;
  old_para text := $q$'Also write a test plan (how the change will be verified). Propose '
      || 'concrete test cases where useful. '$q$;
  new_para text := $q$'Also write a test plan — but read this carefully, because '
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
      || 'tests themselves. '$q$;
begin
  select prosrc into def
  from pg_proc p
  join pg_namespace n on n.oid = p.pronamespace
  where n.nspname = 'public' and p.proname = 'baked_worker_instruction';

  if def is null then
    raise exception 'baked_worker_instruction not found';
  end if;
  if position('acceptance-level cases in the' in def) > 0 then
    raise notice '175 is already applied; leaving it alone';
    return;
  end if;
  if position(old_para in def) = 0 then
    raise exception
      'the test-plan paragraph is not where 175 expects it — 174 must land '
      'first, and baked_worker_instruction must not have drifted since. '
      'Re-derive this edit from its current definition rather than '
      'replacing it wholesale';
  end if;

  execute
    'create or replace function public.baked_worker_instruction(p_kind text) '
    || 'returns text language sql immutable as $fn$'
    || replace(def, old_para, new_para)
    || '$fn$';
end
$migration$;

update public.worker_instructions
set content = public.baked_worker_instruction('plan')
where run_kind = 'plan' and updated_by is null;
