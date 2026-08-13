-- US-34.1: an MCP server catalog, with credentials that stay in the factory.
--
-- us-31.9 gave an agent ONE MCP server: the factory itself, so it can pull code,
-- read context and hand back. That fixed how an agent talks to the factory. It
-- did nothing about the tools an agent needs to do the actual work — an agent
-- asked to implement a feature and verify it has no browser, no database client,
-- no documentation lookup, and nothing beyond a shell command. It writes code and
-- asserts it works.
--
-- Most useful MCP servers need a credential, and that is the whole difficulty.
-- The invariant is explicit: an agent machine holds exactly ONE kind of secret,
-- one worker token per slot. Model keys never land there because the gateway
-- mints short-lived scoped keys; GitHub credentials never land there because
-- cloning goes through the git proxy. A Supabase service key in an `mcp.json` on
-- an agent box would break the one rule the fleet's security rests on, and turn a
-- compromised machine from "N revocable tokens" into "the org's credentials".
--
-- So the catalog lives here and the secrets live in Vault. us-34.2 is what an
-- agent can actually reach, deliberately separate so that security surface is
-- reviewed on its own.

create table public.mcp_servers (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,

  -- What it is, not just where it is: a preset can reference it (us-34.3) and a
  -- manager can see what enabling it grants BEFORE enabling it.
  name text not null,
  slug text not null,
  description text not null default '',

  -- `http` for a streamable-HTTP endpoint, `stdio` for a command the runner
  -- launches. Both are real: Playwright is a command, Sentry is an endpoint.
  transport text not null check (transport in ('http', 'stdio')),
  endpoint text,
  command text,

  -- Declared, so the grant is legible. Not discovered, because a catalog that
  -- has to be reachable to be readable is a catalog that goes blank in an
  -- outage.
  declared_tools text[] not null default '{}',

  -- Credential-free servers are FIRST CLASS, not a lesser case: Playwright, a
  -- filesystem scope, a docs lookup — these need no secret and close the largest
  -- capability gap on their own. They must not be gated behind the credentialed
  -- path's complexity.
  needs_credential boolean not null default false,
  -- The header or env var the credential is presented as, so the proxy knows how
  -- to use a secret it can read and the agent cannot.
  credential_header text,

  -- Write-only, exactly as `llm_providers` does it. At most a last-four ever
  -- comes back out.
  vault_secret_id uuid,
  key_last4 text,

  enabled boolean not null default true,

  -- The result of the last validation, so a registration that cannot be reached
  -- says so where it was entered (us-27.13's rule) rather than at the first run
  -- that needed it.
  last_checked_at timestamptz,
  last_check_ok boolean,
  last_check_error text,

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),

  unique (org_id, slug),
  unique (org_id, name),
  unique (id, org_id),

  -- An http entry needs an endpoint; a stdio entry needs a command. A row that
  -- satisfies neither is a row nothing can launch.
  constraint mcp_servers_has_a_target check (
    (transport = 'http' and coalesce(endpoint, '') <> '')
    or (transport = 'stdio' and coalesce(command, '') <> '')
  )
);

create index mcp_servers_org_idx on public.mcp_servers (org_id, name);

alter table public.mcp_servers enable row level security;

-- Readable by the org — a manager choosing tools for a preset has to see them.
-- The key material is not in any readable column: `vault_secret_id` is a
-- pointer, and reading a Vault secret requires the service role.
create policy "org members read mcp servers"
  on public.mcp_servers for select
  using (public.is_org_member(org_id));

-- Writes go through the API, which is where validation lives. No write policy
-- exists, so no client can register an unvalidated server.

create trigger mcp_servers_touch
  before update on public.mcp_servers
  for each row execute function public.touch_updated_at();

-- ---------------------------------------------------------------------------
-- The credential, write-only
-- ---------------------------------------------------------------------------
-- Deliberately the same shape as `set_llm_provider_key` (migration 151),
-- including its refusals. There is no reason to invent a second pattern for the
-- same problem, and a second pattern is a second thing to get wrong.
create or replace function public.set_mcp_server_key(p_server uuid, p_key text)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  v_org uuid;
  v_secret_id uuid;
begin
  select org_id, vault_secret_id into v_org, v_secret_id
  from public.mcp_servers where id = p_server;
  -- Existence and membership answer identically, so the RPC never confirms a
  -- server id to a non-member.
  if v_org is null or not public.is_org_member(v_org) then
    raise exception 'not authorized';
  end if;
  if p_key is null or length(p_key) < 4 then
    raise exception 'invalid credential';
  end if;
  -- US-31.4's shape guard, for the same reason: browser autofill put an email
  -- address into an API key field once already, and a stored credential that
  -- fails every call while claiming to be set is worse than a named refusal.
  if p_key ~ '\s' then
    raise exception 'that does not look like a credential (it contains whitespace)';
  end if;
  if position('@' in p_key) > 0 then
    raise exception 'that looks like an email address, not a credential — check for browser autofill';
  end if;

  if v_secret_id is null then
    v_secret_id := vault.create_secret(p_key, 'mcp_server_key:' || p_server::text);
  else
    perform vault.update_secret(v_secret_id, p_key);
  end if;

  update public.mcp_servers
  set vault_secret_id = v_secret_id,
      key_last4 = right(p_key, 4)
  where id = p_server;
end;
$$;

comment on function public.set_mcp_server_key(uuid, text) is
  'US-34.1: write-only MCP server credential. Mirrors set_llm_provider_key — the '
  'key goes to Vault and only a last-four ever comes back.';
