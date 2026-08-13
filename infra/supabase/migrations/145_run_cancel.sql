-- 145_run_cancel: "this run should not have been dispatched" (US-27.10).
--
-- runs.status allowed exactly queued | running | succeeded | failed. On
-- 2026-07-26 six plan runs were dispatched in error against stories that
-- already held approved plans, and retiring them meant writing `failed` with
-- an error string explaining that they had never run — a lie the schema
-- forced. Pause keeps a run in the queue forever; reset sends it back to the
-- pool to be claimed again, which is the opposite of what a mis-dispatch
-- needs.
--
-- `cancelled` is terminal and is NOT a failure: nothing is wrong with the
-- machine, so it must not colour a worker's health or the incident feed.
--
-- ONE DELIBERATE OMISSION: a cancelled run does not get `finished_at`. It
-- gets `cancelled_at`. Besides being the truth — the run never finished doing
-- anything — this keeps it out of `activity_feed`'s run-finished branch,
-- whose text is "<kind> run failed" for anything that did not succeed. The
-- feed instead carries the `run-cancelled` issue event, which reads
-- "run cancelled" and carries the reason. Every surface that asks "is this
-- run over" reads `status`, which is the column that answers it.

alter table public.runs drop constraint if exists runs_status_check;
alter table public.runs
  add constraint runs_status_check
  check (status in ('queued', 'running', 'succeeded', 'failed', 'cancelled'));

alter table public.runs add column if not exists cancel_reason text;
alter table public.runs add column if not exists cancelled_at timestamptz;
alter table public.runs add column if not exists cancelled_by uuid;

comment on column public.runs.cancel_reason is
  'US-27.10: why this run was retired without running. Required — the queue '
  'is a shared surface and "why did this disappear" is asked days later. '
  'Set at request time on a running run, which lands `cancelled` when the '
  'worker acknowledges the cooperative stop.';

comment on column public.runs.cancelled_at is
  'US-27.10: when it was cancelled. Deliberately NOT finished_at — a '
  'cancelled run never finished, and activity_feed keys its "run failed" '
  'row off finished_at.';

-- A cancelled run is not claimable. The pool only ever offers `queued`, so
-- this is belt and braces against a future caller that reads more broadly.
create index if not exists runs_cancelled_idx
  on public.runs (org_id, cancelled_at desc)
  where status = 'cancelled';
