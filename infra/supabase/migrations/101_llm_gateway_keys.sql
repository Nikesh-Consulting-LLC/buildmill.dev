-- 101_llm_gateway_keys: scoped ephemeral keys for the runner LLM gateway (US-10.3).
--
-- The supervisor runner holds NO provider keys. Its brain reasons via the
-- `llm.infer` socket relay (server-side, Vault-keyed); its CLI modules point at
-- the server LLM gateway authenticated by a SHORT-LIVED scoped key minted here.
-- A key is bound to {org, worker, run, route} and expires — a leaked module key
-- can't be replayed against another model or after its run. Keys are minted and
-- validated ONLY by the API service role; the table never returns key material,
-- so it has no client RLS policies (default-deny blocks all browser access).

create table public.llm_gateway_keys (
  id uuid primary key default gen_random_uuid(),
  key_hash text not null unique,
  org_id uuid not null references public.organizations(id) on delete cascade,
  worker_id uuid not null references public.workers(id) on delete cascade,
  run_id uuid,
  route text not null default 'runner_brain',
  created_at timestamptz not null default now(),
  expires_at timestamptz not null,
  revoked_at timestamptz
);

create index llm_gateway_keys_hash_idx on public.llm_gateway_keys (key_hash);
create index llm_gateway_keys_worker_idx on public.llm_gateway_keys (worker_id, created_at desc);

alter table public.llm_gateway_keys enable row level security;
-- No policies on purpose: only the service role touches this table.
