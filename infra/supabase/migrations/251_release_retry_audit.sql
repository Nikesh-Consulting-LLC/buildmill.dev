-- US-90.1: a failed release retries; a rejected one is final.
--
-- Who asked for a prep attempt: null on the automatic dispatch that fires
-- when a release is cut, a user id when a manager clicked Retry. The attempt
-- rows themselves are the audit trail — release_prep_runs and
-- deployment_runs (release_id) persist across retries, so the release detail
-- can list every attempt instead of presenting the latest as a clean first
-- try.

alter table public.release_prep_runs
  add column if not exists requested_by uuid references auth.users (id)
    on delete set null;

comment on column public.release_prep_runs.requested_by is
  'US-90.1: the manager who clicked Retry; null when the cut itself queued this attempt.';
