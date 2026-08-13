-- 131_release_run_contract: the release run becomes a real job (US-21.3).
--
-- The `release` instruction has always described reference material for a run
-- kind that was never dispatched ("not a run you are dispatched for"). It is
-- now the contract for one agent job that reads the change range, writes both
-- sets of notes, deploys the pinned commit to UAT and verifies it, and authors
-- the release's regression test cases.
--
-- HOW THIS REBUILDS baked_worker_instruction — read before editing.
-- Migrations 095, 105 and 106 record the same lesson being learned twice: a
-- `create or replace` retyped from an older migration's body silently dropped
-- cases that had been added since, and every new project then failed to seed.
-- So this does not retype the function at all. It reads the CURRENT source out
-- of pg_proc, replaces only the segment between `when 'release' then` and
-- `when 'deploy' then`, and re-creates it. Every other kind is carried
-- verbatim by construction, and the block raises rather than proceeding if the
-- release case is not found.

do $mig$
declare
  v_src text;
  v_new_src text;
  v_text text := $rel$You are preparing ONE release: the one your claimed run names. Read what actually changed FIRST with get_release_changes — the commits, the changed files, and the work items in the range. Never infer a release's contents from the current tree, and if the range comes back truncated, say so in the notes instead of writing around it. Then write two things. notes_summary: a few lines a manager reads at a glance, whose title carries the release version exactly as the factory computed it. notes_detail: what a reviewer actually needs — database and schema changes, migrations applied, modules affected, and anything operationally risky. The version is read from the release, never chosen by you. Then ship it: trigger_deployment sends the release's PINNED commit to the project's UAT deployment, get_deployment_run_status polls it, and get_deployment_health verifies it. Never claim an outcome you did not observe, and never submit a release whose deployment did not succeed — report the failure and stop. Finally, author regression test cases for the release as a whole: integration across the included work items, and anything the migrations imply. They are attached alongside the cases those work items already carry, for a human to run by hand. Finish with submit_release_run. If your work context says you are resuming, pick up from what is already done rather than redoing it. Nothing promotes to Production from this run — the manager's UAT sign-off is what unlocks that.$rel$;
begin
  select prosrc into v_src
  from pg_proc p
  join pg_namespace n on n.oid = p.pronamespace
  where n.nspname = 'public' and p.proname = 'baked_worker_instruction';

  if v_src is null then
    raise exception 'baked_worker_instruction not found';
  end if;

  v_new_src := regexp_replace(
    v_src,
    'when ''release'' then.*?(?=when ''deploy'' then)',
    'when ''release'' then ' || quote_literal(v_text) || E'\n    '
  );

  if v_new_src = v_src then
    raise exception
      'the release case was not found in baked_worker_instruction — '
      'refusing to rebuild it blind (see the 095/105/106 lesson above)';
  end if;

  execute
    'create or replace function public.baked_worker_instruction(p_kind text) '
    || 'returns text language sql immutable as $fn$' || v_new_src || '$fn$';
end
$mig$;

-- Projects that never customised their release instruction get the new
-- contract; anything a manager has edited (updated_by is not null) is left
-- exactly as they wrote it.
update public.worker_instructions
set content = public.baked_worker_instruction('release')
where run_kind = 'release' and updated_by is null;

-- ---------------------------------------------------------------------------
-- A test case can belong to a release (US-21.3 attaches them; US-21.4 runs
-- and approves them)
-- ---------------------------------------------------------------------------
-- `issue_id` has been nullable since 031, so this follows the shape already
-- there rather than inventing a second test-case model.

alter table public.test_cases
  add column if not exists release_id uuid
    references public.releases(id) on delete cascade;

create index if not exists test_cases_release_idx
  on public.test_cases (release_id) where release_id is not null;

comment on column public.test_cases.release_id is
  'US-21.3/21.4: the release this case belongs to. Inherited cases are COPIED '
  'from the included work items, not referenced, so a later edit to a work '
  'item cannot change the set that was run against a shipped build.';
