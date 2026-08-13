-- 019_servers: deployment server registry (US-1.28). Org-scoped, RLS via
-- is_org_member(). Only non-secret metadata lives in this table: the
-- credential material (password / SSH private key / passphrase) is
-- written by `api` (service role) to the private `data` Storage bucket
-- under <org_id>/servers/<server_id>/, and is never returned to any
-- client. The only readable trace of a stored key is key_fingerprint;
-- host_key_fingerprint is captured on first successful connect (TOFU).
--
-- Storage note: the `data` bucket is private and has NO storage.objects
-- policies, so RLS default-deny already blocks every client (even the
-- owning org's own members, with their own JWT) from reading, listing,
-- or downloading anything in it. Credentials flow browser -> api ->
-- Storage and are readable only with the service role. We deliberately
-- do NOT add any client storage policy for this bucket.

create table public.servers (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  name text not null,
  host text not null,
  port int not null default 22 check (port between 1 and 65535),
  username text not null,
  auth_method text not null check (auth_method in ('password', 'ssh_key')),
  key_fingerprint text,       -- SHA256:... of the stored private key's public half
  host_key_fingerprint text,  -- remote host key, captured on first successful connect
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (org_id, name)
);

create index servers_org_idx on public.servers (org_id);

alter table public.servers enable row level security;

create policy "members manage their org servers"
  on public.servers for all
  using (public.is_org_member(org_id))
  with check (public.is_org_member(org_id));

create trigger servers_touch
  before update on public.servers
  for each row execute function public.touch_updated_at();
