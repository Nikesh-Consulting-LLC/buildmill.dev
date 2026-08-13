-- 210_run_analytics_indexes: US-62.1's report groups and filters `runs` by
-- (kind, status, created_at) and by (worker_id, kind, created_at) on every
-- dimension switch and window change. Neither index existed — the table's
-- only indexes predate the kind/worker_id dimensions this report slices by.

create index if not exists runs_kind_status_created_idx
  on public.runs (kind, status, created_at);

create index if not exists runs_worker_kind_created_idx
  on public.runs (worker_id, kind, created_at);
