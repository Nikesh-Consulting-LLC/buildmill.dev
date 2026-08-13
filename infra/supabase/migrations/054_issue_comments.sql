-- 054_issue_comments: comments on work items (US-5.12).
--
-- A flat, immutable, chronological thread attached to the issue — the
-- ambient conversation between org members and working agents. Members
-- write under RLS from the browser; workers write through `api`
-- (service role) via the claim-guarded worker endpoint / MCP add_comment.
-- A blocking question that needs the manager's answer stays us-5.4's job.

create table public.issue_comments (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  issue_id uuid not null,
  author_kind text not null check (author_kind in ('user', 'worker')),
  author_user uuid,
  author_worker uuid references public.workers(id) on delete set null,
  run_id uuid references public.runs(id) on delete set null,
  body text not null check (length(trim(body)) > 0),
  created_at timestamptz not null default now(),
  foreign key (issue_id, org_id)
    references public.issues (id, org_id) on delete cascade,
  check (
    (author_kind = 'user' and author_user is not null)
    or (author_kind = 'worker' and author_worker is not null)
  )
);

create index issue_comments_issue_idx
  on public.issue_comments (issue_id, created_at);

alter table public.issue_comments enable row level security;

create policy "members read their org issue comments"
  on public.issue_comments for select
  using (public.is_org_member(org_id));

-- Members post as themselves only; worker rows arrive via service role
-- (bypasses RLS). No update/delete policies — the thread is immutable v1.
create policy "members post issue comments as themselves"
  on public.issue_comments for insert
  with check (
    public.is_org_member(org_id)
    and author_kind = 'user'
    and author_user = auth.uid()
  );

-- Live thread updates in the web panel.
alter table public.issue_comments replica identity full;
alter publication supabase_realtime add table public.issue_comments;
