-- 047_github_install_rpc: replace the install callback's direct-Postgres
-- write with a service-role RPC (US-3.19). The callback has no user JWT
-- (GitHub redirects the browser here directly) — trust comes from the
-- signed state token `api` already verified, not from auth.uid()/RLS, so
-- this function is callable only by the service role, never by
-- authenticated users or anon directly.

create or replace function public.record_github_app_installation(
  p_org uuid,
  p_installation_id bigint,
  p_account_login text,
  p_account_type text,
  p_connected_by uuid
)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into public.github_connections
    (org_id, method, installation_id, account_login, account_type, connected_by)
  values (p_org, 'app', p_installation_id, p_account_login, p_account_type, p_connected_by)
  on conflict (installation_id) do update set
    org_id = excluded.org_id,
    account_login = excluded.account_login,
    account_type = excluded.account_type,
    connected_by = excluded.connected_by,
    updated_at = now();
end;
$$;

revoke execute on function public.record_github_app_installation(uuid, bigint, text, text, uuid)
  from public, anon, authenticated;
grant execute on function public.record_github_app_installation(uuid, bigint, text, text, uuid)
  to service_role;
