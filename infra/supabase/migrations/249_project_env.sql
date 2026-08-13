-- 249_project_env (US-89.2): the environment is defined once.
--
-- One place per project answering "what does an agent get when it works
-- here?" — the Supabase login of the app under development, a SQL
-- connection string, an API key, a flag. Each entry is a name, a kind
-- (plain | secret), a description (the MCP discovery answer is
-- self-documenting), and an optional agent scope (null = every agent
-- granted the project).
--
-- Secret VALUES never live in this table. They follow the us-1.28 server-
-- credential pattern: written browser → api → the private `data` Storage
-- bucket at <org>/projects/<project>/env/<entry-id>, readable by the api's
-- service role only (that bucket has no storage.objects policies — RLS
-- default-deny IS the guarantee). The row keeps a fingerprint so the UI can
-- say "Set · a1b2c3d4" without ever reading the value back.

create table public.project_env (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  project_id uuid not null references public.projects(id) on delete cascade,
  -- null = every agent with access to the project; set = that agent only.
  agent_id uuid references public.workers(id) on delete cascade,

  name text not null check (name ~ '^[A-Z][A-Z0-9_]*$' and length(name) <= 80),
  kind text not null default 'plain' check (kind in ('plain', 'secret')),
  -- plain: the value, readable by org members like any config.
  -- secret: always null here; the value lives in the private bucket.
  value text check (kind = 'plain' or value is null),
  -- secret only: first 8 hex of sha256, for the "Set · <fp>" caption.
  fingerprint text,
  description text not null default '',

  updated_by uuid,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index project_env_org_idx on public.project_env (org_id);
create index project_env_project_idx on public.project_env (project_id, name);

-- One definition per (project, scope, name): a project-wide FOO and an
-- agent-scoped FOO may coexist (the scoped one wins at delivery); two
-- project-wide FOOs may not.
create unique index project_env_name_key
  on public.project_env (project_id, name, coalesce(agent_id, '00000000-0000-0000-0000-000000000000'::uuid));

alter table public.project_env enable row level security;

-- Org members read and write rows (plain values included — they are config,
-- not credentials). Secret values are unreachable by construction: the
-- column is null and the bucket refuses the client role.
create policy "org members read project env"
  on public.project_env for select
  using (public.is_org_member(org_id));
create policy "org members insert project env"
  on public.project_env for insert
  with check (public.is_org_member(org_id));
create policy "org members update project env"
  on public.project_env for update
  using (public.is_org_member(org_id));
create policy "org members delete project env"
  on public.project_env for delete
  using (public.is_org_member(org_id));

create trigger project_env_touch
  before update on public.project_env
  for each row execute function public.touch_updated_at();
