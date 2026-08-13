-- 060_learning_submissions: audit trail for worker-contributed learnings
-- (US-5.6). Each MCP submit_learning call records who contributed what
-- before the text flows into the US-1.21 LLM merge — the curated
-- learnings document keeps no attribution, so this table is where the
-- manager sees which worker contributed which discovery.
--
-- The API inserts over its direct Postgres connection (workers are not
-- Supabase users); org members read under RLS. No client writes.

create table public.learning_submissions (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  project_id uuid not null references public.projects(id) on delete cascade,
  worker_id uuid references public.workers(id) on delete set null,
  text text not null,
  created_at timestamptz not null default now()
);

create index learning_submissions_project_idx
  on public.learning_submissions (project_id, created_at desc);

alter table public.learning_submissions enable row level security;

create policy "members read their org learning submissions"
  on public.learning_submissions for select
  using (public.is_org_member(org_id));
