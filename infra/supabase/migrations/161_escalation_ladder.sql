-- US-33.4: repair escalates instead of repeating.
--
-- us-31.5 stopped an agent retrying an item forever. It did not make the retries
-- any different from each other: attempt two runs with exactly the settings that
-- failed at attempt one, which is the definition of superstition with a bill
-- attached.
--
-- Escalation uses the SUPERVISOR override layer us-32.7 already resolves and
-- records — so it is visible and explainable by construction rather than being a
-- hidden behaviour of the retry path.

-- ---------------------------------------------------------------------------
-- The ladder, declared on the presets themselves
-- ---------------------------------------------------------------------------
-- A preset names what it escalates to, and the chain ends. Without a declared
-- next step there is no escalation — so no run can climb forever, and the ladder
-- is org configuration rather than a rule buried in code.
alter table public.agent_presets
  add column if not exists escalates_to uuid
    references public.agent_presets(id) on delete set null;

comment on column public.agent_presets.escalates_to is
  'US-33.4: the preset a failed run of this one escalates to. Null ends the ladder.';

-- Seed the obvious chain in every org: Fast → Balanced → Deep → (end).
-- `Investigate` is deliberately NOT the top of it: plan mode refuses every edit,
-- so escalating a failed code run into it would guarantee a run that changes
-- nothing.
update public.agent_presets p
set escalates_to = up.id
from public.agent_presets up
where up.org_id = p.org_id
  and p.escalates_to is null
  and (
    (p.template_key = 'fast' and up.template_key = 'balanced')
    or (p.template_key = 'balanced' and up.template_key = 'deep')
  );

-- ---------------------------------------------------------------------------
-- What kind of failure it was
-- ---------------------------------------------------------------------------
-- Only failure classes that more capability could plausibly answer escalate. A
-- transient network error escalates nothing — retrying at higher effort would be
-- expensive superstition. us-10.11's classifier already decides work-fault vs
-- runner-fault and the runner already sends it; it was simply never stored.
alter table public.runs
  add column if not exists fault_class text
    check (fault_class is null or fault_class in ('work-fault', 'runner-fault'));

comment on column public.runs.fault_class is
  'US-10.11 classification as reported at hand-back. US-33.4 escalates only on '
  'work-fault: a broken box is not answered by thinking harder.';
