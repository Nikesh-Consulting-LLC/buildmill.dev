-- 127_org_queue_hold_reasons: expose run_hold_reason to the manager's queue (US-17.5).
--
-- The /factory-queue page (a direct Supabase read) computed "held" itself from
-- a sibling-draft query, so a feature/epic-mode hold showed as plain "queued"
-- while the pool actually held it — the exact shown-vs-enforced divergence
-- us-15.2 warns against. This RPC returns run_hold_reason for the org's queued
-- runs so the page renders the same held state the pool enforces, with the
-- mode's reason. security definer + an is_org_member gate so it can read the
-- underlying tables without leaking across orgs.

create or replace function public.org_queue_hold_reasons(p_org uuid)
returns table(run_id uuid, reason text)
language sql
stable
security definer
set search_path to public
as $$
  select r.id, public.run_hold_reason(r.id)
  from public.runs r
  where r.org_id = p_org
    and r.status = 'queued'
    and public.is_org_member(p_org)
$$;

revoke execute on function public.org_queue_hold_reasons(uuid) from public, anon;
grant execute on function public.org_queue_hold_reasons(uuid) to authenticated;
