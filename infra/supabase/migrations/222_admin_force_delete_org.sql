-- 222_admin_force_delete_org: lets the platform-admin org delete actually
-- force through the queued/running guard (046_force_delete_issues.sql).
--
-- That guard's escape hatch is the `force_delete_issues` RPC, which requires
-- the caller be an ACTIVE MEMBER of the org (is_org_member) — right for the
-- Issues page, where a member force-deletes their own org's stuck issues.
-- Wrong for /admin/orgs: a platform admin operates via the API's service-role
-- key and is very often not a member of the org they're deleting, so that
-- RPC always raised "Not authorized" for them, and the org DELETE fell
-- straight through to the raw trigger error ("Cannot delete an issue that is
-- queued or running") with no way past it.
--
-- This is the service-role-only counterpart: no membership check (the API's
-- own require_platform_admin dependency is the authorization), sets the same
-- transaction-local escape hatch, then deletes the org so every FK cascade —
-- issues included — rides through in one transaction.
create or replace function public.admin_force_delete_org(p_org_id uuid)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  perform set_config('app.force_delete_issues', 'on', true);
  -- US-1.27 follow-up (migration 225): a project's own deletion tries to log
  -- itself into content_audit with the org_id that's about to stop existing.
  perform set_config('app.org_being_deleted', 'on', true);
  delete from public.organizations where id = p_org_id;
end;
$$;

revoke execute on function public.admin_force_delete_org(uuid) from public, anon, authenticated;
grant execute on function public.admin_force_delete_org(uuid) to service_role;
