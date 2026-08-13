-- 044_github_connections: org GitHub connections with a method column
-- (US-3.15). Generalizes 010's github_installations: method='app' rows
-- carry the App installation id; method='pat' rows carry a fine-grained
-- PAT held write-only in Vault (the 002 set_llm_api_key pattern) plus an
-- explicit repo list, because a PAT's grants can't be enumerated via the
-- GitHub API the way an installation's can.

create table public.github_connections (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  method text not null check (method in ('app', 'pat')),
  account_login text not null,
  account_type text not null check (account_type in ('User', 'Organization')),
  installation_id bigint unique,
  pat_last4 text,
  pat_expires_at timestamptz,
  vault_secret_id uuid,
  repos jsonb not null default '[]'::jsonb,
  connected_by uuid not null references auth.users(id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint app_rows_have_installation
    check (method <> 'app' or installation_id is not null)
);

create index github_connections_org_idx on public.github_connections (org_id);

alter table public.github_connections enable row level security;

create policy "members manage their org github connections"
  on public.github_connections for all
  using (public.is_org_member(org_id))
  with check (public.is_org_member(org_id));

create trigger github_connections_updated_at
  before update on public.github_connections
  for each row execute function public.touch_updated_at();

-- Existing App installations carry over; timestamps follow.
insert into public.github_connections
  (org_id, method, installation_id, account_login, account_type,
   connected_by, created_at, updated_at)
select org_id, 'app', installation_id, account_login, account_type,
       connected_by, created_at, updated_at
from public.github_installations;

drop table public.github_installations;

-- Write-only PAT storage. security definer (owner: postgres) so the
-- function can reach the vault schema; callers never can. The api
-- validates the token against GitHub before calling this.
create or replace function public.connect_github_pat(
  p_org uuid,
  p_token text,
  p_account_login text,
  p_account_type text,
  p_expires_at timestamptz,
  p_repos jsonb
)
returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
  v_id uuid;
  v_secret_id uuid;
begin
  if not public.is_org_member(p_org) then
    raise exception 'not authorized';
  end if;
  if p_token is null or length(p_token) < 8 then
    raise exception 'invalid token';
  end if;
  if p_repos is null or jsonb_typeof(p_repos) <> 'array'
     or jsonb_array_length(p_repos) = 0 then
    raise exception 'at least one repository is required';
  end if;

  v_id := gen_random_uuid();
  v_secret_id := vault.create_secret(p_token, 'github_pat:' || v_id::text);

  insert into public.github_connections
    (id, org_id, method, account_login, account_type,
     pat_last4, pat_expires_at, vault_secret_id, repos, connected_by)
  values
    (v_id, p_org, 'pat', coalesce(p_account_login, ''),
     coalesce(p_account_type, 'User'), right(p_token, 4), p_expires_at,
     v_secret_id, p_repos, auth.uid());

  return v_id;
end;
$$;

-- Disconnect any connection: removes the Vault secret (pat rows) along
-- with the row. GitHub-side App uninstall happens in the api first.
create or replace function public.delete_github_connection(p_id uuid)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  v_secret_id uuid;
  v_org uuid;
begin
  select org_id, vault_secret_id into v_org, v_secret_id
  from public.github_connections where id = p_id;
  if v_org is null then
    raise exception 'connection not found';
  end if;
  if not public.is_org_member(v_org) then
    raise exception 'not authorized';
  end if;
  if v_secret_id is not null then
    delete from vault.secrets where id = v_secret_id;
  end if;
  delete from public.github_connections where id = p_id;
end;
$$;

revoke execute on function public.connect_github_pat(uuid, text, text, text, timestamptz, jsonb) from public, anon;
grant execute on function public.connect_github_pat(uuid, text, text, text, timestamptz, jsonb) to authenticated;
revoke execute on function public.delete_github_connection(uuid) from public, anon;
grant execute on function public.delete_github_connection(uuid) to authenticated;
