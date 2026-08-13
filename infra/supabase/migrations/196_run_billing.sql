-- 196_run_billing: how a run was billed (US-52.4).
--
-- A subscription-mode Claude run (us-52.1) bypasses the metered gateway, so
-- it has zero llm_usage rows FOREVER — by design, not by failure. Without
-- this column that reads exactly like the succeeded-but-unmetered gap
-- us-33.1's meter is supposed to be closing. Stamped at claim from the
-- resolved `auth` setting; stamped rather than joined, because the agent's
-- setting can change later and the question is how THIS run was billed.

alter table public.runs
  add column if not exists billing text not null default 'metered'
  check (billing in ('metered', 'subscription'));

comment on column public.runs.billing is
  'US-52.4: metered = priced via the llm gateway (us-33.1); subscription =
   billed to a Claude subscription, deliberately off-meter (zero llm_usage
   rows is correct for these). Stamped at claim from the resolved auth
   setting.';
