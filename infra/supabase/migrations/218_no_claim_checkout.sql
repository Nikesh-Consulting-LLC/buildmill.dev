-- 218_no_claim_checkout: an MCP worker can browse/read/download a
-- project's repository it has access to WITHOUT holding a claimed run —
-- for exploration, testing a connection, or checking code out before
-- deciding what to work on. Opt-in per worker, on by default, so
-- today's behavior for every existing worker is unchanged until someone
-- deliberately turns it off.
--
-- Authorization otherwise reuses exactly what already gates git
-- clone/fetch through the factory remote with no claim in play
-- (worker_allowed_for_project / worker_has_grant, US-3.12/31.3) — this
-- is the same access, over MCP instead of git.

alter table public.workers
  add column no_claim_checkout boolean not null default true;
