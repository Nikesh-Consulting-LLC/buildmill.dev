-- 254_view_costs_capability: the Costs section's door key (us-95.1).
--
-- Phase 95 gives cost reporting its own top-level section, visible to an
-- org's owner and admin. The gate is a capability in the US-9.2 grid — a new
-- row, not a hard-coded role pair — so the client gate, the API's
-- has_org_capability() checks, and the US-9.3 superadmin editor all resolve
-- through the same data and cannot disagree.
--
-- Deliberately NOT wired into any RLS policy: `llm_usage` stays readable by
-- every org member because member-visible surfaces read it (the project
-- card's spend line, us-91.14's item costs, the agent page summary). This
-- capability gates the Costs *section* and its section-only endpoints, not
-- the ledger.

alter table public.role_capabilities
  drop constraint role_capabilities_capability_check;
alter table public.role_capabilities
  add constraint role_capabilities_capability_check
  check (capability in ('manage_org', 'manage_members', 'manage_project',
                        'manage_work', 'review_work', 'develop', 'view',
                        'view_costs'));

-- Seed every role explicitly (the US-9.3 editor toggles existing rows and
-- renders an absent one as a gap — 208's lesson). Owner and admin hold the
-- key; everyone else, including the checks-nothing 'agent' role, does not.
insert into public.role_capabilities (role, capability, allowed) values
  ('owner',     'view_costs', true),
  ('admin',     'view_costs', true),
  ('lead',      'view_costs', false),
  ('developer', 'view_costs', false),
  ('reviewer',  'view_costs', false),
  ('viewer',    'view_costs', false),
  ('agent',     'view_costs', false)
on conflict (role, capability) do update set allowed = excluded.allowed;
