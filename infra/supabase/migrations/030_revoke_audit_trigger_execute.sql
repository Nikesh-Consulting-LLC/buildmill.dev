-- 030_revoke_audit_trigger_execute: least-privilege hardening flagged by
-- the Supabase security advisor after 027. The US-1.49 audit triggers are
-- SECURITY DEFINER; Postgres refuses to run trigger functions outside a
-- trigger anyway, but they should not be callable via /rest/v1/rpc at all.

revoke execute on function public.log_deployment_config_change()
  from public, anon, authenticated;
revoke execute on function public.log_deployment_notifications_change()
  from public, anon, authenticated;
