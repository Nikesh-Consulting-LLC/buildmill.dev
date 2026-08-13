-- 212_api_request_log: US-62.8 -- an API request says where its time went.
-- No app-wide request timing existed before this (the only precedent,
-- mcp_catalog.py's duration_ms capture, was scoped to one router's tool-call
-- audit log). One row per request: total duration, and how much of it was
-- spent in the database (apps/api/app/db.py's _TimedConnection wrapper).
--
-- Superadmin-only observability data, same shape as agent_servers/
-- platform_llm_key: RLS enabled, no policies at all -- default-deny for
-- every client role, read only by the API's own service-role connection via
-- the /admin/* routes.

create table public.api_request_log (
  id bigserial primary key,
  route text not null,
  method text not null,
  status_code int not null,
  duration_ms int not null,
  db_ms int not null,
  created_at timestamptz not null default now()
);

create index api_request_log_route_created_idx
  on public.api_request_log (route, created_at desc);

alter table public.api_request_log enable row level security;
