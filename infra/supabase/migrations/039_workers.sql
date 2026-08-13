-- 039_workers: worker registry & tokens (US-3.1).
--
-- A worker is anything that can claim work from the pool and hand it
-- back — the autonomous runner or a person driving their own tool.
-- Tokens follow the write-only secret pattern (002 / set_llm_api_key):
-- only a SHA-256 hash + last4 are stored; the plaintext is returned
-- exactly once by the minting RPCs and never by any other path.
-- Revoked workers stay listed (audit trail) and cannot be un-revoked;
-- regenerate issues a fresh token on the same row instead.

create table public.workers (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  name text not null,
  type text not null check (type in ('autonomous', 'human')),
  user_id uuid references auth.users(id) on delete set null,
  token_hash text not null,
  token_last4 text not null,
  status text not null default 'active' check (status in ('active', 'revoked')),
  last_seen_at timestamptz,
  created_at timestamptz not null default now()
);

create index workers_org_idx on public.workers (org_id);
create index workers_token_hash_idx on public.workers (token_hash);

alter table public.workers enable row level security;

create policy "members manage their org workers"
  on public.workers for all
  using (public.is_org_member(org_id))
  with check (public.is_org_member(org_id));

-- Token minting. security definer like 002's key RPCs; the token is
-- generated server-side, hashed at rest, and returned exactly once.
create or replace function public.create_worker(
  p_org uuid,
  p_name text,
  p_type text,
  p_user_id uuid default null
)
returns table (worker_id uuid, token text)
language plpgsql
security definer
set search_path = public
as $$
declare
  v_token text;
begin
  if not public.is_org_member(p_org) then
    raise exception 'not authorized';
  end if;
  if p_name is null or length(trim(p_name)) = 0 then
    raise exception 'name required';
  end if;
  if p_type not in ('autonomous', 'human') then
    raise exception 'invalid worker type';
  end if;
  if p_user_id is not null and not exists (
    select 1 from public.organization_members m
    where m.org_id = p_org and m.user_id = p_user_id
  ) then
    raise exception 'linked user is not an org member';
  end if;

  v_token := 'sfw_' || encode(extensions.gen_random_bytes(24), 'hex');

  return query
  insert into public.workers (org_id, name, type, user_id, token_hash, token_last4)
  values (p_org, trim(p_name), p_type, p_user_id,
          encode(extensions.digest(v_token, 'sha256'), 'hex'),
          right(v_token, 4))
  returning id, v_token;
end;
$$;

create or replace function public.regenerate_worker_token(p_worker uuid)
returns text
language plpgsql
security definer
set search_path = public
as $$
declare
  v_org uuid;
  v_token text;
begin
  select org_id into v_org from public.workers where id = p_worker;
  if v_org is null or not public.is_org_member(v_org) then
    raise exception 'not authorized';
  end if;

  v_token := 'sfw_' || encode(extensions.gen_random_bytes(24), 'hex');

  update public.workers
  set token_hash = encode(extensions.digest(v_token, 'sha256'), 'hex'),
      token_last4 = right(v_token, 4),
      status = 'active'
  where id = p_worker;

  return v_token;
end;
$$;

revoke execute on function public.create_worker(uuid, text, text, uuid) from public, anon;
grant execute on function public.create_worker(uuid, text, text, uuid) to authenticated;
revoke execute on function public.regenerate_worker_token(uuid) from public, anon;
grant execute on function public.regenerate_worker_token(uuid) to authenticated;
