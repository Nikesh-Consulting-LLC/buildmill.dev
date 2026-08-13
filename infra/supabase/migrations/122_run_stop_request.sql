-- 122_run_stop_request: a cooperative "stop work" signal on a run (US-15.15).
--
-- us-15.14 forces a run back to a clean queued state from outside, without the
-- agent's help. This is the nicer path: the manager asks the working agent to
-- stop, the agent sees the request on its next report_progress heartbeat,
-- undoes its own partial work with its own tools, and acknowledges — landing
-- the item in the same clean pre-dispatch state, but cleaned by the agent that
-- knows exactly what it touched. The forced path (us-15.14) remains the
-- guarantee when the agent never cooperates.

alter table public.runs add column if not exists stop_requested_at timestamptz;

comment on column public.runs.stop_requested_at is
  'US-15.15: when the manager asked the working agent to stop. Surfaced to the '
  'claim-holder on its next report_progress; the agent cleans up and calls '
  'acknowledge_stop. Null once the run is re-queued.';
