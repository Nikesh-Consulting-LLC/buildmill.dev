-- US-38.1 — a cache read is not priced as fresh input.
--
-- us-33.1's meter reads the two Anthropic cache fields and adds them into the
-- same total as fresh input, then multiplies the lot by one rate. Those three
-- things are not priced the same: a cache read bills at 0.1x the input rate and
-- a cache write at 1.25x, so a cached token has been charged nine to twelve
-- times what it cost. On a workload running 67 input tokens per output token,
-- that is most of the bill.

-- The two classes, as SUBSETS of tokens_in -----------------------------------
--
-- tokens_in keeps meaning "all input tokens". Every existing aggregate reads
-- it — the Spend page, the per-run rollup, and us-37.1's project budget — and
-- redefining it to mean fresh-only would silently change every historical
-- figure in the app on the day this ships.
--
-- Nullable, and NULL means "this row predates the split", NOT "zero cache".
-- History does not get cheaper retroactively on the strength of an assumption.
alter table public.llm_usage
  add column if not exists cache_read_tokens integer,
  add column if not exists cache_write_tokens integer;

comment on column public.llm_usage.cache_read_tokens is
  'US-38.1: input tokens served from the provider prompt cache. A SUBSET of '
  'tokens_in, not a sibling of it. NULL means the row predates the split — '
  'never that there was no caching.';
comment on column public.llm_usage.cache_write_tokens is
  'US-38.1: input tokens written into the provider prompt cache. A SUBSET of '
  'tokens_in. NULL means the row predates the split. OpenAI-shaped providers '
  'report reads only, so a NULL here beside a non-null read is a real shape, '
  'not a gap.';

-- The rates ------------------------------------------------------------------
--
-- Both nullable, and an unset cache rate charges the FULL input rate — exactly
-- today's behaviour. This follows us-33.1's standing rule that unknown cost
-- must never read as free: guessing 0.1x against a provider that does not price
-- that way is an underestimate the manager cannot see, and us-36.4 already made
-- correcting a rate a screen action rather than a migration.
alter table public.llm_model_prices
  add column if not exists cache_read_per_mtok numeric(12,4),
  add column if not exists cache_write_per_mtok numeric(12,4);

comment on column public.llm_model_prices.cache_read_per_mtok is
  'US-38.1: dollars per million cache-READ input tokens. NULL charges them at '
  'input_per_mtok — the rate they have always been charged at — so no figure '
  'silently drops without a rate being set.';
comment on column public.llm_model_prices.cache_write_per_mtok is
  'US-38.1: dollars per million cache-WRITE input tokens. NULL charges them at '
  'input_per_mtok.';

-- Neither class can exceed the total it is a subset of. Written as a constraint
-- rather than trusted to the meter, because double counting here inflates every
-- downstream figure including a project budget that now stops work.
alter table public.llm_usage
  drop constraint if exists llm_usage_cache_within_input;
alter table public.llm_usage
  add constraint llm_usage_cache_within_input
  check (
    coalesce(cache_read_tokens, 0) + coalesce(cache_write_tokens, 0)
      <= coalesce(tokens_in, 0)
    or tokens_in is null
  );
