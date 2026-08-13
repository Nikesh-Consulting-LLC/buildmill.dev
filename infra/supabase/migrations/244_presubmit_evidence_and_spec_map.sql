-- 244_presubmit_evidence_and_spec_map: a code run proves its tests ran
-- (US-81.6), and names the specs it wrote (US-81.5).
--
-- projects.presubmit_test_command: the project's declared FAST test command —
-- the gate a coding agent runs in its workspace before submitting. Phase 80's
-- lesson applies verbatim: a slow gate is a skipped gate, so this is the
-- Essential-style suite, never the full QA run (that belongs to the UAT
-- pipeline, where machine time is cheap).
--
-- runs.test_evidence: what the worker reported at submit — command, exit
-- code, counts, a bounded output tail. Worker-reported and labeled as such in
-- review: a signal, not factory-observed proof, and it never feeds the
-- release gate.
--
-- runs.spec_map: the case→spec linkage a code run reports via report_spec_map
-- ([{test_case_id, suite_id, spec_ref}]). Held on the run and applied to the
-- cases when the work item merges — a rejected changeset must not leave cases
-- claiming automation by specs that never landed.

alter table public.projects
  add column presubmit_test_command text;

alter table public.runs
  add column test_evidence jsonb,
  add column spec_map jsonb;

comment on column public.projects.presubmit_test_command is
  'US-81.6: the fast pre-submit test command a code run must execute in its '
  'workspace. The fast suite, deliberately - a slow gate is a skipped gate.';

comment on column public.runs.test_evidence is
  'US-81.6: worker-reported pre-submit test outcome {command, exit_code, '
  'passed, failed, skipped, output_tail}. A review signal, not proof.';

comment on column public.runs.spec_map is
  'US-81.5: [{test_case_id, suite_id, spec_ref}] reported at submit, applied '
  'to test_cases when the work item merges.';
