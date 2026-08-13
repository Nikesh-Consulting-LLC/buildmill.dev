-- 219_github_connection_unique_per_org: one GitHub App installation may back
-- more than one workspace.
--
-- US-3.19 (migration 047) made installation_id globally unique and had the
-- install callback upsert `on conflict (installation_id) do update set
-- org_id = excluded.org_id`. Two orgs that legitimately share one GitHub
-- App installation — the common case when several workspaces build repos
-- under the same GitHub organization — therefore could not both hold it:
-- connecting the second silently *moved* the row off the first, leaving that
-- workspace with no credential at all. Its clones then fell through to the
-- env-token fallback and failed against GitHub with a 401 that named neither
-- the org nor the cause. Re-connecting the first workspace only moved the row
-- back, breaking the second — a see-saw with no winning move.
--
-- Uniqueness belongs per org: one connection per (org, installation).

alter table public.github_connections
  drop constraint if exists github_connections_installation_id_key;
drop index if exists public.github_connections_installation_id_key;

create unique index if not exists github_connections_org_installation_key
  on public.github_connections (org_id, installation_id);

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
  on conflict (org_id, installation_id) do update set
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
