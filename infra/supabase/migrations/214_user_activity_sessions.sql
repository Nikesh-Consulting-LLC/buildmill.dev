-- 214_user_activity_sessions: US-62.6 -- a review dialog times itself.
--
-- Every existing timestamp pair (issue_events "ready" -> approvals.created_at,
-- us-62.5) measures queue-inclusive LATENCY, not active effort -- nothing
-- records when a human actually opened something to look at it. This is the
-- real thing: `active_ms` is a pause-aware total (paused on a backgrounded
-- tab and on inactivity), so a dialog left open during a meeting doesn't
-- inflate a manager's "time reviewing" number. `ended_at - started_at` is
-- kept alongside it as the raw wall-clock span, so "was mostly idle" stays
-- visible rather than hidden by the pause logic that produced active_ms.
--
-- Insert-only from the browser under RLS, matching client_perf_events
-- (migration 213) and "build less API" — no new endpoint.

create table public.user_activity_sessions (
  id bigserial primary key,
  org_id uuid not null references public.organizations(id) on delete cascade,
  user_id uuid not null,
  issue_id uuid,
  -- 'prd' | 'plan' | 'code-review' | 'artifact-edit'
  kind text not null,
  started_at timestamptz not null,
  ended_at timestamptz not null,
  active_ms int not null,
  created_at timestamptz not null default now()
);

create index user_activity_sessions_issue_idx
  on public.user_activity_sessions (issue_id) where issue_id is not null;
create index user_activity_sessions_user_created_idx
  on public.user_activity_sessions (user_id, created_at desc);

alter table public.user_activity_sessions enable row level security;

create policy "authenticated users record their own activity sessions"
  on public.user_activity_sessions for insert
  to authenticated
  with check (
    user_id = auth.uid()
    and public.is_org_member(org_id)
  );
