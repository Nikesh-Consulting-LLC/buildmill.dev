-- 193_platform_admin_requires_active_membership: close the suspended-admin hole.
--
-- is_platform_admin() granted global admin to ANY member of a platform-admin
-- org, without checking organization_members.status. Migration 089 made every
-- other membership helper (is_org_member, is_org_owner, has_org_capability)
-- require status = 'active' so suspension actually blocks, but this one was
-- missed: a suspended platform-org member kept platform admin over every org,
-- because has_org_capability short-circuits through is_platform_admin() before
-- it ever reaches its own status check.
--
-- SURGERY OVER THE LIVE BODY per the drift rules (see 172/174/176/185/187):
-- read prosrc, assert the anchor occurs exactly once, insert after it, and
-- raise (rolling back) when the live body is not where this expects it. The
-- bodies on prod and dev were verified byte-identical — and identical to 086's
-- definition — on 2026-07-29 before this was written.

do $migration$
declare
  def text;
  anchor text := 'and o.is_platform_admin = true';
  addition text;
begin
  addition := chr(10) || '      and m.status = ' || quote_literal('active');

  select prosrc into def
  from pg_proc p
  join pg_namespace n on n.oid = p.pronamespace
  where n.nspname = 'public' and p.proname = 'is_platform_admin';

  if def is null then
    raise exception 'is_platform_admin not found';
  end if;
  if position('m.status' in def) > 0 then
    raise notice '193 is already applied; leaving is_platform_admin alone';
    return;
  end if;
  if (length(def) - length(replace(def, anchor, ''))) / length(anchor) <> 1 then
    raise exception
      'is_platform_admin has drifted from where 193 expects it (the anchor '
      'must occur exactly once) — re-derive this insertion from the live '
      'body rather than replacing the function wholesale';
  end if;

  execute
    'create or replace function public.is_platform_admin() '
    || 'returns boolean language sql stable security definer '
    || 'set search_path = public as $fn$'
    || replace(def, anchor, anchor || addition)
    || '$fn$';
end
$migration$;
