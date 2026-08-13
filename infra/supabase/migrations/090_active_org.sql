-- 090_active_org: persisted active-org selection for the org switcher (US-9.7).
--
-- A human in more than one org picks which one they're working in; the choice
-- persists across sessions and every org-scoped page resolves through it
-- (falling back to the first membership when unset or no longer valid). Stored
-- on principals so it is server-readable. Agents are single-org and never set
-- it. on delete set null so leaving/deleting the active org self-heals.
alter table public.principals
  add column active_org_id uuid references public.organizations(id) on delete set null;
