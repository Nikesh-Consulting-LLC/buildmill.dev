-- US-32.5: a preset is a named bundle of run settings.
--
-- Until now an agent's only tuning surface was a model id per run kind.
-- Everything else that decides how a run goes — reasoning effort, a fallback
-- chain, permission mode, turn and spend ceilings, standing instructions —
-- either did not exist in the app or lived in `RUNNER_CLAUDE_ARGS` on the
-- machine, invisible and unmanaged. Configuring nine fields per agent does not
-- scale and gives no answer to "how do we do code runs here". A preset is that
-- answer.
--
-- The hard constraint: `llm_providers` is org-scoped with a manually curated
-- model list per org, and since us-27.8 the gateway resolves a provider BY
-- matching a model id against that list. So a platform-level template cannot
-- name `claude-sonnet-5` — in another org that string may name nothing, or
-- route somewhere else entirely.
--
-- Hence two tables. Superadmin authors TEMPLATES, which carry everything a
-- preset needs EXCEPT a model, plus advice about what model suits them. Each org
-- holds REAL preset rows seeded from those templates; a preset's model is null
-- ("inherit the org default") until someone sets one, and a set model is
-- validated against that org's own providers at the API. Nothing abstract,
-- nothing indirect, and us-27.8's provider-from-model rule is untouched.

-- ---------------------------------------------------------------------------
-- preset_templates — platform level, superadmin-authored
-- ---------------------------------------------------------------------------
create table public.preset_templates (
  id uuid primary key default gen_random_uuid(),
  key text not null unique,
  name text not null,
  description text not null default '',

  -- The canonical setting names from us-32.4, minus `model`: effort,
  -- permission_mode, max_turns, max_budget_usd, fallback_model,
  -- standing_instructions. A module that cannot express one of them says so
  -- (us-32.4) rather than the preset pretending otherwise.
  settings jsonb not null default '{}'::jsonb,

  -- Advice, not a value: "a strong model" cannot be resolved to an id the
  -- platform is allowed to assume any org has.
  model_hint text not null default '',

  sort_order int not null default 0,
  -- Bumped by the trigger below. An org's copy records which version it came
  -- from, so a re-seed can say what would change.
  version int not null default 1,

  updated_by uuid,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

alter table public.preset_templates enable row level security;

-- Readable org-wide (a manager comparing their copy against the template needs
-- to see it); writable only through the superadmin-gated API, which holds the
-- service role. No write policy exists, so no client can author one.
create policy "any member reads preset templates"
  on public.preset_templates for select
  to authenticated
  using (true);

create trigger preset_templates_touch
  before update on public.preset_templates
  for each row execute function public.touch_updated_at();

create or replace function public.bump_preset_template_version()
returns trigger
language plpgsql
as $$
begin
  -- Only a change to what a preset DOES is a new version; renaming the
  -- template or reordering the list is not something to re-seed over.
  if new.settings is distinct from old.settings
     or new.model_hint is distinct from old.model_hint then
    new.version := old.version + 1;
  end if;
  return new;
end;
$$;

create trigger preset_templates_version
  before update on public.preset_templates
  for each row execute function public.bump_preset_template_version();

-- ---------------------------------------------------------------------------
-- agent_presets — the org's own rows
-- ---------------------------------------------------------------------------
create table public.agent_presets (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,

  -- Provenance. Null for a preset the org invented itself, which is a
  -- first-class thing to do — a re-seed never touches those.
  template_key text,
  -- The template version this copy was seeded from, so a template edit can be
  -- offered as an explicit re-seed that says what would change.
  seeded_version int,

  name text not null,
  description text not null default '',

  -- Concrete, and validated against this org's `llm_providers.models` at the
  -- API. Null means "inherit the org's default for that call" — the same
  -- convention the model-route table already uses.
  model text,

  settings jsonb not null default '{}'::jsonb,

  -- Incremented by the trigger below on any change to what the preset does.
  -- Runs record the preset AND its version, so "Deep got worse last week" is
  -- answerable (us-33.6).
  version int not null default 1,

  sort_order int not null default 0,
  archived_at timestamptz,

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),

  unique (org_id, name),
  unique (id, org_id)
);

create index agent_presets_org_idx on public.agent_presets (org_id, sort_order);

alter table public.agent_presets enable row level security;

-- Read for the org; every write goes through the API, which is the only place
-- the model can be checked against the org's providers and the settings against
-- the module declarations. A client-writable preset is an unvalidated preset.
create policy "org members read their presets"
  on public.agent_presets for select
  using (public.is_org_member(org_id));

create trigger agent_presets_touch
  before update on public.agent_presets
  for each row execute function public.touch_updated_at();

create or replace function public.bump_agent_preset_version()
returns trigger
language plpgsql
as $$
begin
  if new.settings is distinct from old.settings
     or new.model is distinct from old.model then
    new.version := old.version + 1;
  end if;
  return new;
end;
$$;

create trigger agent_presets_version
  before update on public.agent_presets
  for each row execute function public.bump_agent_preset_version();

-- ---------------------------------------------------------------------------
-- What a run ran under
-- ---------------------------------------------------------------------------
-- Recorded here so that editing a preset provably cannot rewrite history:
-- a finished run keeps the version it ran under. us-32.7 populates these.
alter table public.runs
  add column if not exists preset_id uuid references public.agent_presets(id)
    on delete set null,
  add column if not exists preset_version int;

-- ---------------------------------------------------------------------------
-- The seeded set
-- ---------------------------------------------------------------------------
-- Reviewable at UAT; the point is that a fresh org is usable without having to
-- design presets before doing any work.
insert into public.preset_templates (key, name, description, settings, model_hint, sort_order)
values
  (
    'fast',
    'Fast',
    'Cheap and quick. For small, well-specified changes where the answer is '
    || 'obvious and the cost of a second attempt is low.',
    jsonb_build_object(
      'effort', 'low',
      'permission_mode', 'bypassPermissions',
      'max_turns', 20,
      'max_budget_usd', 2
    ),
    'Your cheapest capable model.',
    10
  ),
  (
    'balanced',
    'Balanced',
    'The default. Enough thinking for ordinary implementation work without '
    || 'paying for reasoning the task does not need.',
    jsonb_build_object(
      'effort', 'medium',
      'permission_mode', 'bypassPermissions',
      'max_turns', 40,
      'max_budget_usd', 5
    ),
    'Your general-purpose model — usually the org default.',
    20
  ),
  (
    'deep',
    'Deep',
    'High effort for hard implementation: unfamiliar code, a change that '
    || 'spans several systems, or a story that has already failed once.',
    jsonb_build_object(
      'effort', 'high',
      'permission_mode', 'bypassPermissions',
      'max_turns', 80,
      'max_budget_usd', 15
    ),
    'Your strongest model. Set a fallback so an overloaded provider does not '
    || 'fail the run.',
    30
  ),
  (
    'investigate',
    'Investigate',
    'Plan-mode first, for root-cause work. Refuses every edit, so it can read '
    || 'anything and change nothing.',
    jsonb_build_object(
      'effort', 'high',
      'permission_mode', 'plan',
      'max_turns', 40,
      'max_budget_usd', 8,
      'standing_instructions',
      'Find the root cause before proposing anything. Quote the evidence — the '
      || 'error, the line, the failing input — and say plainly when the '
      || 'evidence does not support a conclusion.'
    ),
    'Your strongest model; this preset is for the runs where thinking is the '
    || 'whole job.',
    40
  );

-- Seeding an org: one copy per template, skipping any name it already uses.
-- SECURITY DEFINER because the org-creation trigger runs as whoever inserted
-- the organization, who has no write policy on agent_presets (nobody does).
create or replace function public.seed_org_presets(p_org uuid)
returns int
language plpgsql
security definer
set search_path = public
as $$
declare
  v_seeded int := 0;
begin
  insert into public.agent_presets
    (org_id, template_key, seeded_version, name, description, settings, sort_order)
  select p_org, t.key, t.version, t.name, t.description, t.settings, t.sort_order
  from public.preset_templates t
  where not exists (
    select 1 from public.agent_presets p
    where p.org_id = p_org
      and (p.template_key = t.key or p.name = t.name)
  );
  get diagnostics v_seeded = row_count;
  return v_seeded;
end;
$$;

revoke all on function public.seed_org_presets(uuid) from public, anon, authenticated;

-- Every existing org gets the set.
select public.seed_org_presets(id) from public.organizations;

-- And every new one, automatically.
create or replace function public.seed_presets_on_new_org()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  perform public.seed_org_presets(new.id);
  return new;
end;
$$;

create trigger organizations_seed_presets
  after insert on public.organizations
  for each row execute function public.seed_presets_on_new_org();
