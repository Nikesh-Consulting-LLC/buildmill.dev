-- 050_dispatch_prd_draft_grants: lock down dispatch_prd_draft's execute grants (US-3.21).
--
-- 049_dispatch_prd_draft created dispatch_prd_draft without the standard
-- revoke/grant pair, leaving it executable by anon/PUBLIC by default. Every
-- other RPC-exposed function in this migration set (including
-- dispatch_issue, which this function mirrors) pairs its `create or replace
-- function` with an explicit lockdown to `authenticated` only. Bring
-- dispatch_prd_draft in line.

revoke execute on function public.dispatch_prd_draft(uuid) from public, anon;
grant execute on function public.dispatch_prd_draft(uuid) to authenticated;
