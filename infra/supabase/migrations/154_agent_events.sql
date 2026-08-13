-- US-32.2: a record of what a manager changed about an agent.
--
-- Run history is attributed to a principal, not to a name, so renaming an agent
-- correctly re-labels its past runs — which is right, and also means a reader
-- who remembers "pod-001-1 failed twice" has no way to find out that agent is
-- now called "frontend". The rename itself has to be on the record.
--
-- Deliberately generic rather than a `agent_renames` table: the rest of Phase 32
-- changes agent-level settings too (presets, standing instructions), and each of
-- those wants the same five columns.

create table public.agent_events (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  -- the agent this happened to. Kept on principal delete? No: an event about a
  -- principal that no longer exists names nothing, so it cascades away with it.
  principal_id uuid not null references public.principals(id) on delete cascade,

  type text not null,
  payload jsonb not null default '{}'::jsonb,

  actor_id uuid,
  actor_email text not null default '',

  created_at timestamptz not null default now()
);

create index agent_events_principal_idx
  on public.agent_events (principal_id, created_at desc);
create index agent_events_org_idx on public.agent_events (org_id, created_at desc);

alter table public.agent_events enable row level security;

-- Readable by the org; written only by the API (service role), which is the
-- only thing that knows who the actor was.
create policy "org members read agent events"
  on public.agent_events for select
  using (public.is_org_member(org_id));
