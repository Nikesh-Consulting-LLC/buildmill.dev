-- US-13.3: an agent can always reach the manager at hand-back.
--
-- The submission itself carries the agent's notes as data: they land on
-- the run row (read by the review surface at the gate) and are mirrored
-- into the work item's comment thread by perform_submit. No separate
-- add_comment call — which a worker-side tool allow-list can silently
-- deny — is ever required for a finding to reach the manager.

alter table public.runs
  add column if not exists handback_notes text;

comment on column public.runs.handback_notes is
  'US-13.3: what the agent wanted the manager to know at hand-back — '
  'shown on the review surface beside the gate decision.';
