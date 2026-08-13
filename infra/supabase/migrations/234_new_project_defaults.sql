-- 234_new_project_defaults (US-74.4): make a new project's Task Processing
-- settings match how the factory is actually run --
--
--   build_mode      'story'  ->  'feature'   (build a feature's stories as a
--                                             batch: every story planned and
--                                             approved before any is coded,
--                                             a later feature waits for the
--                                             earlier one)
--   sequential_only  true    ->   false      (drop the global one-issue-at-a-
--                                             time dispatch lock: with the
--                                             feature owning the build unit,
--                                             independent work may overlap)
--
-- The pairing is deliberate. `build_mode = 'feature'` already orders the
-- build structurally through run_hold_reason -- an earlier feature must
-- finish, and stories inside a feature drain in sub_no order -- so the extra
-- project-wide serialization of `sequential_only` mostly stalls unrelated
-- work rather than preventing collisions.
--
-- DEFAULTS ONLY -- deliberately no backfill. Postgres applies a column
-- default to new rows only, and existing projects keep whatever their
-- manager chose (or inherited). Changing settled projects' build behavior
-- underneath a running pipeline is not a migration's business.
--
-- No behavior change to dispatch_issue / run_hold_reason: they read these
-- columns, and this only changes what a fresh row starts with.

alter table public.projects
  alter column build_mode set default 'feature';

alter table public.projects
  alter column sequential_only set default false;

comment on column public.projects.build_mode is
  'story = route any story freely, one at a time. feature = a feature owns '
  'the build of its stories (all planned and approved, then all coded; a '
  'later feature waits for the earlier one). epic = the same, one level up. '
  'Defaults to feature (US-74.4).';

comment on column public.projects.sequential_only is
  'When true, no issue in this project may be dispatched (plan or code) '
  'while another non-abandoned issue in the project is planning, in '
  'plan-review, planned (approved plan, code not yet dispatched), queued, '
  'running, needs-fixes, in-review, or failed. Composes with build_mode. '
  'Defaults to false since US-74.4 -- build_mode feature already orders the '
  'build, and the project-wide lock on top of it stalls unrelated work.';
