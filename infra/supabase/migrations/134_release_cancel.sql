-- 134_release_cancel: cancel a release that hasn't started (US-23.1).
--
-- A mis-cut release currently has no way out: the partial unique index from
-- 130 allows one release in flight per project, so the mistake blocks the next
-- release until an agent picks its run up and drives it all the way to a
-- rejection.
--
-- `cancelled` is its own status, deliberately not `rejected`. A rejection means
-- a build reached UAT and failed it — there is a tested artifact and a reason.
-- A cancelled release was never built or tested, and collapsing the two would
-- have the release history claim testing that never happened.
--
-- It is NOT added to releases_one_in_flight_per_project's status list, so
-- cancelling frees the project immediately.

alter table public.releases drop constraint if exists releases_status_check;

alter table public.releases add constraint releases_status_check
  check (status in (
    'queued',
    'running',
    'uat-deployed',
    'uat-signed-off',
    'promoting',
    'released',
    'rolled-back',
    'rejected',
    'cancelled',
    'failed'
  ));

alter table public.releases
  add column if not exists cancelled_at timestamptz,
  add column if not exists cancelled_by uuid;

comment on column public.releases.cancelled_at is
  'US-23.1: abandoned before an agent started. Distinct from rejected_at, '
  'which means a built-and-tested release failed UAT.';
