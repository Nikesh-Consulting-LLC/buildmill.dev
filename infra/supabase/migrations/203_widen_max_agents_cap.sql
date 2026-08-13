-- 203_widen_max_agents_cap: 202's upper bound (100) was an arbitrary guess
-- mirroring max_item_attempts's shape, disproven within the hour by real
-- data — a dev org already carries 289 agent-kind organization_members rows
-- (Phase 26 deliberately never deletes the principal/membership behind a
-- removed agent, "so past runs still name who did them," and this org has
-- accumulated years of that). The cap exists to catch fat-finger entry, not
-- to bound real usage, so it moves to something no real org will approach.

alter table public.organizations
  drop constraint organizations_max_agents_check;

alter table public.organizations
  add constraint organizations_max_agents_check
  check (max_agents between 1 and 100000);
