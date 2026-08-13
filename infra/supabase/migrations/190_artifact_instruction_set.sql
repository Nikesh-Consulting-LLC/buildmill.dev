-- 190_artifact_instruction_set: a document keeps the brief it was written
-- from (US-49.2).
--
-- `issues.instruction_set` is a LIVING field, and US-49.1 has just made
-- editing it easy — at dispatch, and mid-run from the runs section. So reading
-- it later answers "what will the next agent read", never "what was this PRD
-- written from". The two drift by design, and once they have, nobody can tell
-- whether a document is odd because the agent misread the ask or because it
-- was told something different at the time.
--
-- The snapshot lives on the ARTIFACT because `artifacts` carries no run_id
-- (031_issues.sql) — joining a document to the instructions behind it would
-- otherwise be a guess through issue and timestamp. On the row, it versions
-- with the document and nothing later can rewrite it.
--
-- Null means "not recorded": every artifact written before this migration,
-- and any version a manager authored by hand. The UI shows no panel rather
-- than an empty one, because an empty box captioned "instructions" reads as
-- "the agent was given none", which is false.
--
-- Filled by the five agent hand-back inserts in apps/api/app/db.py — plan,
-- test_plan, prd, wireframe, elaboration — at HAND-BACK rather than at
-- dispatch: the value the agent could last have read is the one in force when
-- it submitted, and US-49.1 exists precisely so a manager can redirect a run
-- in flight.

alter table public.artifacts add column instruction_set text;

comment on column public.artifacts.instruction_set is
  'US-49.2: the item''s instruction set as it stood when this version was handed back. Null on manager-authored versions and on anything written before migration 190.';
