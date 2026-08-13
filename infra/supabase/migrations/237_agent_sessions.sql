-- 237_agent_sessions: a session with no work item (US-78.10).
--
-- Every interactive session so far belongs to a run: dispatched from the pool,
-- leased, submitted, reviewed. This gives the same machinery a second owner —
-- a session a manager opens directly, to explore a codebase or try an approach
-- without first inventing a work item for it.
--
-- Deliberately NOT a run. A run has a lease, a claim, an item and a review
-- gate; a session has none of those and pretending otherwise would put rows in
-- `runs` that every dispatch query then has to learn to ignore.

create table public.agent_sessions (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  project_id uuid not null references public.projects(id) on delete cascade,
  -- the agent holding it. Kept on delete so a closed session still names who
  -- ran it, the same rule agent_slots follows for retired identities.
  worker_id uuid references public.workers(id) on delete set null,
  -- who opened it. A session is visible to the org's managers (AC8), so this
  -- is attribution, not ownership.
  created_by uuid references public.principals(id) on delete set null,

  -- the CLI's own session id, so reopening resumes rather than restarts
  -- (US-78.9's `session/load`). Null until the runner reports one.
  acp_session_id text,
  workspace_path text,

  status text not null default 'opening'
    check (status in ('opening', 'open', 'closed', 'failed')),
  -- why it ended, in the CLI's words where there are any
  error text,

  created_at timestamptz not null default now(),
  -- AC3: idle sessions time out. Bumped on every event the runner reports, so
  -- "idle" means the AGENT has been quiet, not that nobody is looking.
  last_active_at timestamptz not null default now(),
  closed_at timestamptz
);

create index agent_sessions_org_idx on public.agent_sessions (org_id, created_at desc);
create index agent_sessions_worker_idx on public.agent_sessions (worker_id)
  where status in ('opening', 'open');

-- AC3: one live session per agent. The slot it holds is the agent itself, and
-- a second session on the same worker would be two conversations fighting over
-- one workspace. Enforced here rather than in the API, because the API is not
-- the only way rows arrive.
create unique index agent_sessions_one_live_per_worker
  on public.agent_sessions (worker_id)
  where status in ('opening', 'open');

alter table public.agent_sessions enable row level security;
create policy "org members read agent sessions"
  on public.agent_sessions for select to authenticated
  using (public.is_org_member(org_id));
-- Writes go through the API (service role): opening one spawns a process on a
-- pool machine, which is not a thing a PostgREST insert should be able to do.

-- ---------------------------------------------------------------------------
-- The transcript. `run_trace` cannot hold these: its `run_id` is NOT NULL and
-- points at `runs`, and a session has no run. Same shape and the same kind
-- vocabulary, so the console renders both without knowing which it is reading.
-- ---------------------------------------------------------------------------
create table public.agent_session_events (
  id bigserial primary key,
  session_id uuid not null references public.agent_sessions(id) on delete cascade,
  org_id uuid not null references public.organizations(id) on delete cascade,
  kind text not null default 'output'
    check (kind in ('step', 'tool', 'decision', 'output', 'progress', 'error')),
  content text not null,
  at timestamptz not null default now()
);
create index agent_session_events_session_idx
  on public.agent_session_events (session_id, at, id);

alter table public.agent_session_events enable row level security;
create policy "org members read agent session events"
  on public.agent_session_events for select to authenticated
  using (public.is_org_member(org_id));

alter publication supabase_realtime add table public.agent_session_events;

-- ---------------------------------------------------------------------------
-- AC5: metering. `llm_usage.run_id` is how spend is attributed today; a
-- session's calls have no run, so the column gains a sibling rather than being
-- overloaded — a nullable FK, and exactly one of the two set.
-- ---------------------------------------------------------------------------
alter table public.llm_usage
  add column if not exists session_id uuid
    references public.agent_sessions(id) on delete set null;
create index if not exists llm_usage_session_idx on public.llm_usage (session_id);
