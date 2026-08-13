-- 059_clarifications: mid-run questions from workers to the manager
-- (US-5.4). A claim-holding worker asks over MCP instead of guessing or
-- releasing the claim; the open question lands in the manager's Things
-- to Do; the answer is appended to the work item's instruction set
-- (US-5.11) so it survives re-dispatch and reaches any future claimer.
--
-- Writes: the API inserts questions over its direct Postgres connection
-- (workers are not Supabase users), so there is no insert policy. Org
-- members read and answer under RLS.

create table public.clarifications (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  issue_id uuid not null,
  run_id uuid not null references public.runs(id) on delete cascade,
  worker_id uuid references public.workers(id) on delete set null,
  question text not null,
  answer text,
  asked_at timestamptz not null default now(),
  answered_at timestamptz,
  answered_by uuid references auth.users(id),
  foreign key (issue_id, org_id)
    references public.issues (id, org_id) on delete cascade
);

create index clarifications_issue_idx
  on public.clarifications (issue_id, asked_at);
create index clarifications_open_idx
  on public.clarifications (org_id, asked_at)
  where answer is null;

alter table public.clarifications enable row level security;

create policy "members read their org clarifications"
  on public.clarifications for select
  using (public.is_org_member(org_id));

create policy "members answer their org clarifications"
  on public.clarifications for update
  using (public.is_org_member(org_id))
  with check (public.is_org_member(org_id));
