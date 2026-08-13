-- US-33.1: the gateway meters every call.
--
-- `runs` has carried `tokens_in`, `tokens_out` and `cost_usd` since migration
-- 005. The worker submit endpoint accepts all three. `ModuleResult` has fields
-- for them. Nothing has ever written them — every run in the system reports null
-- spend, and there has never been a usage table.
--
-- The gateway is the only place that can fix that: every model call from every
-- module passes through it on a scoped key that already names the run and the
-- model. A CLI's own `--output-format json` total would work for Claude and not
-- for the others, and would miss subagent spend — which is where a session's
-- cost actually goes.

-- ---------------------------------------------------------------------------
-- llm_usage — one append-only row per call
-- ---------------------------------------------------------------------------
-- Events, not counters. Aggregates are queries (us-33.3): a mutable counter
-- would drift and could not be recomputed from anything.
create table public.llm_usage (
  id bigserial primary key,
  org_id uuid not null references public.organizations(id) on delete cascade,

  -- Null for a call with no run behind it (a thinking function, or a brain key
  -- minted before us-27.8). Kept on run delete: the money was still spent.
  run_id uuid,
  worker_id uuid,
  project_id uuid,

  provider_id uuid,
  provider_type text not null default '',
  provider_name text not null default '',
  model text not null default '',
  route text not null default '',

  tokens_in int,
  tokens_out int,

  -- US-33.1: a provider shape whose usage cannot be read is recorded as
  -- UNPARSED, never as zero. A zero is indistinguishable from a free call and
  -- would quietly understate every total in the system.
  parsed boolean not null default false,
  parse_note text,

  -- The rate in force at the time, on the row. Cost is derived; storing a
  -- computed number with no record of the rate lets a repriced model rewrite
  -- history.
  rate_in_per_mtok numeric(12, 4),
  rate_out_per_mtok numeric(12, 4),
  cost_usd numeric(12, 6),

  status_code int,
  created_at timestamptz not null default now()
);

create index llm_usage_org_idx on public.llm_usage (org_id, created_at desc);
create index llm_usage_run_idx on public.llm_usage (run_id) where run_id is not null;
create index llm_usage_attribution_idx
  on public.llm_usage (org_id, worker_id, project_id, model);

alter table public.llm_usage enable row level security;

-- Readable by the org; written only by the API (service role) — the gateway is
-- the only thing that knows what a call cost.
create policy "org members read their usage"
  on public.llm_usage for select
  using (public.is_org_member(org_id));

-- ---------------------------------------------------------------------------
-- llm_model_prices — the rate, as org configuration
-- ---------------------------------------------------------------------------
-- Tokens are the measured fact; money is tokens times a price the org sets.
-- Per million tokens because that is how every provider quotes it, and because
-- per-token rates in a numeric column lose precision at the fourth decimal.
create table public.llm_model_prices (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  model text not null,
  input_per_mtok numeric(12, 4) not null default 0,
  output_per_mtok numeric(12, 4) not null default 0,
  updated_at timestamptz not null default now(),
  created_at timestamptz not null default now(),
  unique (org_id, model)
);

alter table public.llm_model_prices enable row level security;

create policy "org members read model prices"
  on public.llm_model_prices for select
  using (public.is_org_member(org_id));

create trigger llm_model_prices_touch
  before update on public.llm_model_prices
  for each row execute function public.touch_updated_at();

-- ---------------------------------------------------------------------------
-- The per-run rollup
-- ---------------------------------------------------------------------------
-- The dead columns on `runs` finally mean something. Recomputed from the usage
-- rows rather than incremented, so it is correct after a late-arriving row and
-- can be rebuilt at any time.
create or replace function public.rollup_run_usage(p_run uuid)
returns void
language sql
as $$
  update public.runs r
  set tokens_in  = u.tokens_in,
      tokens_out = u.tokens_out,
      cost_usd   = u.cost_usd
  from (
    select coalesce(sum(tokens_in), 0)  as tokens_in,
           coalesce(sum(tokens_out), 0) as tokens_out,
           -- Null, not zero, when nothing priced: "we do not know" and "it was
           -- free" are different answers.
           nullif(coalesce(sum(cost_usd), 0), 0) as cost_usd
    from public.llm_usage
    where run_id = p_run
  ) u
  where r.id = p_run;
$$;

comment on function public.rollup_run_usage(uuid) is
  'US-33.1: recompute runs.tokens_in/out/cost_usd from the append-only llm_usage rows.';
