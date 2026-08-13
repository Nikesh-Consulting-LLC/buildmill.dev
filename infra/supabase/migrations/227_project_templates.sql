-- 227_project_templates: Project Templates (Phase 67, us-67.1).
--
-- A superadmin-authored bundle of guideline sections, per-run-kind worker
-- instructions, and the two project-shaped thinking prompts
-- (test_case_elaborate, deploy_script_generate) that a project silently
-- inherits a COPY of at creation — mirroring preset_templates/agent_presets
-- (migration 157, us-32.5) one level down: platform template -> org copy ->
-- project seeding, instead of platform template -> org copy -> live read.
--
-- Model/effort/turn-ceiling/attempt-limit config is untouched — that stays
-- exclusively platform_run_config per Phase 57 (us-57.6/us-57.7). A template
-- only changes what CONTENT gets seeded into worker_instructions/
-- project_guidelines at project-creation time; nothing here is read live by
-- assemble_project_guidelines/worker_instruction_for.

-- ---------------------------------------------------------------------------
-- project_templates — platform level, superadmin-authored
-- ---------------------------------------------------------------------------
create table public.project_templates (
  id uuid primary key default gen_random_uuid(),
  key text not null unique,
  name text not null,
  description text not null default '',
  category text not null default '',
  is_default boolean not null default false,
  sort_order int not null default 0,
  version int not null default 1,
  updated_by uuid,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- Exactly one default template — the one a brand-new org/project inherits.
create unique index project_templates_one_default
  on public.project_templates ((true))
  where is_default;

alter table public.project_templates enable row level security;

-- Readable org-wide (browsing the catalog before copying); writable only
-- through the superadmin-gated API (us-67.2), which holds the service role.
create policy "any member reads project templates"
  on public.project_templates for select
  to authenticated
  using (true);

create trigger project_templates_touch
  before update on public.project_templates
  for each row execute function public.touch_updated_at();

-- ---------------------------------------------------------------------------
-- project_template_sections — the bundle content
-- ---------------------------------------------------------------------------
create table public.project_template_sections (
  id uuid primary key default gen_random_uuid(),
  template_id uuid not null references public.project_templates(id) on delete cascade,
  section_type text not null check (section_type in ('guideline', 'worker_instruction', 'prompt')),
  section_key text not null,
  title text not null default '',
  content text not null default '',
  sort_order int not null default 0,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (template_id, section_type, section_key)
);

create index project_template_sections_template_idx
  on public.project_template_sections (template_id, sort_order);

alter table public.project_template_sections enable row level security;

create policy "any member reads project template sections"
  on public.project_template_sections for select
  to authenticated
  using (true);

create trigger project_template_sections_touch
  before update on public.project_template_sections
  for each row execute function public.touch_updated_at();

-- Bump the parent template's version whenever a section's content actually
-- changes shape — mirrors bump_preset_template_version (migration 157).
create or replace function public.bump_project_template_version()
returns trigger
language plpgsql
as $$
declare
  v_template_id uuid;
begin
  v_template_id := coalesce(new.template_id, old.template_id);
  update public.project_templates
    set version = version + 1
    where id = v_template_id;
  return coalesce(new, old);
end;
$$;

create trigger project_template_sections_version
  after insert or update or delete on public.project_template_sections
  for each row execute function public.bump_project_template_version();

-- ---------------------------------------------------------------------------
-- org_project_templates — the org's own copies + custom templates
-- ---------------------------------------------------------------------------
create table public.org_project_templates (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  -- Provenance. Null means org-invented from scratch (mirrors
  -- agent_presets.template_key) — a first-class thing to do.
  template_key text,
  seeded_version int,
  name text not null,
  description text not null default '',
  is_default boolean not null default false,
  -- Owner/Admin can hide a copied template from the (future) project-creation
  -- picker without deleting it.
  is_available boolean not null default true,
  sort_order int not null default 0,
  archived_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (org_id, name)
);

create index org_project_templates_org_idx
  on public.org_project_templates (org_id, sort_order);

-- Exactly one default per org — what a new project in that org inherits.
create unique index org_project_templates_one_default
  on public.org_project_templates (org_id)
  where is_default;

alter table public.org_project_templates enable row level security;

create policy "org members read their project templates"
  on public.org_project_templates for select
  using (public.is_org_member(org_id));

-- Plain CRUD gated on manage_project (no external value needs server-side
-- validation the way agent_presets.model does), per "Build less API".
create policy "manage_project inserts org project templates"
  on public.org_project_templates for insert
  with check (public.has_org_capability(org_id, 'manage_project'));

create policy "manage_project updates org project templates"
  on public.org_project_templates for update
  using (public.has_org_capability(org_id, 'manage_project'))
  with check (public.has_org_capability(org_id, 'manage_project'));

create policy "manage_project deletes org project templates"
  on public.org_project_templates for delete
  using (public.has_org_capability(org_id, 'manage_project'));

create trigger org_project_templates_touch
  before update on public.org_project_templates
  for each row execute function public.touch_updated_at();

-- ---------------------------------------------------------------------------
-- org_project_template_sections — the org's editable copy of the content
-- ---------------------------------------------------------------------------
create table public.org_project_template_sections (
  id uuid primary key default gen_random_uuid(),
  org_template_id uuid not null references public.org_project_templates(id) on delete cascade,
  org_id uuid not null references public.organizations(id) on delete cascade,
  section_type text not null check (section_type in ('guideline', 'worker_instruction', 'prompt')),
  section_key text not null,
  title text not null default '',
  content text not null default '',
  sort_order int not null default 0,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (org_template_id, section_type, section_key)
);

create index org_project_template_sections_template_idx
  on public.org_project_template_sections (org_template_id, sort_order);
create index org_project_template_sections_org_idx
  on public.org_project_template_sections (org_id);

alter table public.org_project_template_sections enable row level security;

create policy "org members read their template sections"
  on public.org_project_template_sections for select
  using (public.is_org_member(org_id));

create policy "manage_project inserts template sections"
  on public.org_project_template_sections for insert
  with check (public.has_org_capability(org_id, 'manage_project'));

create policy "manage_project updates template sections"
  on public.org_project_template_sections for update
  using (public.has_org_capability(org_id, 'manage_project'))
  with check (public.has_org_capability(org_id, 'manage_project'));

create policy "manage_project deletes template sections"
  on public.org_project_template_sections for delete
  using (public.has_org_capability(org_id, 'manage_project'));

create trigger org_project_template_sections_touch
  before update on public.org_project_template_sections
  for each row execute function public.touch_updated_at();

-- ---------------------------------------------------------------------------
-- projects.org_template_id — provenance, set silently at creation
-- ---------------------------------------------------------------------------
alter table public.projects
  add column org_template_id uuid references public.org_project_templates(id) on delete set null;

-- ---------------------------------------------------------------------------
-- worker_instructions gains the two project-shaped thinking prompts
-- (story_breakdown stays global-only — it has no live call site today).
-- ---------------------------------------------------------------------------
alter table public.worker_instructions
  drop constraint if exists worker_instructions_run_kind_check;
alter table public.worker_instructions
  add constraint worker_instructions_run_kind_check
  check (run_kind in (
    'prd', 'plan', 'code', 'release', 'breakdown', 'test', 'deploy',
    'guidelines', 'elaborate', 'wireframe',
    'story_breakdown', 'test_case_elaborate', 'deploy_script_generate'
  ));

-- ---------------------------------------------------------------------------
-- copy_project_template_into_org — the "add this template to my org" action
-- ---------------------------------------------------------------------------
create or replace function public.copy_project_template_into_org(
  p_template_id uuid, p_org uuid, p_name text
)
returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
  v_template public.project_templates%rowtype;
  v_org_template_id uuid;
begin
  if not public.has_org_capability(p_org, 'manage_project') then
    raise exception 'not authorized';
  end if;

  select * into v_template from public.project_templates where id = p_template_id;
  if not found then
    raise exception 'template not found';
  end if;

  insert into public.org_project_templates
    (org_id, template_key, seeded_version, name, description, sort_order)
  values
    (p_org, v_template.key, v_template.version, p_name, v_template.description, v_template.sort_order)
  returning id into v_org_template_id;

  insert into public.org_project_template_sections
    (org_template_id, org_id, section_type, section_key, title, content, sort_order)
  select v_org_template_id, p_org, s.section_type, s.section_key, s.title, s.content, s.sort_order
  from public.project_template_sections s
  where s.template_id = p_template_id;

  return v_org_template_id;
end;
$$;

revoke all on function public.copy_project_template_into_org(uuid, uuid, text) from public, anon;
grant execute on function public.copy_project_template_into_org(uuid, uuid, text) to authenticated;

-- ---------------------------------------------------------------------------
-- Draft the "Default" template from today's baked content — not an empty
-- shell. Every existing project and org already runs on exactly this text.
-- ---------------------------------------------------------------------------
insert into public.project_templates (key, name, description, category, is_default, sort_order)
values (
  'default', 'Default',
  'The factory''s baked-in starting point, drafted from today''s factory defaults so nothing '
  || 'changes for an existing project.',
  'General', true, 0
);

-- Worker-instruction sections, one per run kind, from the live
-- baked_worker_instruction() — always current, since that function has been
-- widened by every migration that added a run kind (077..185).
insert into public.project_template_sections (template_id, section_type, section_key, content, sort_order)
select t.id, 'worker_instruction', k.kind, public.baked_worker_instruction(k.kind), k.ord
from public.project_templates t
cross join (values
  ('prd', 1), ('plan', 2), ('code', 3), ('release', 4), ('breakdown', 5),
  ('test', 6), ('deploy', 7), ('guidelines', 8), ('elaborate', 9), ('wireframe', 10)
) as k(kind, ord)
where t.key = 'default'
  and public.baked_worker_instruction(k.kind) is not null;

-- Guideline sections — only the ones the factory already auto-seeds into
-- every new project today (buildmill-workflow: migration 055; release:
-- migration 076). The other catalog keys are "Add section" pre-fills, not
-- auto-seeded defaults, so drafting the Default template from them would
-- change today's behavior for a project that customizes nothing.
insert into public.project_template_sections (template_id, section_type, section_key, title, content, sort_order)
select t.id, 'guideline', 'buildmill-workflow', 'Working with Build Mill',
       public.effective_guideline_section('buildmill-workflow'), 998
from public.project_templates t where t.key = 'default';

insert into public.project_template_sections (template_id, section_type, section_key, title, content, sort_order)
select t.id, 'guideline', 'release', 'Versioning & Release',
       public.default_guidelines_release_section(), 999
from public.project_templates t where t.key = 'default';

-- The two project-shaped thinking prompts, verbatim from today's
-- LLM_FUNCTIONS templates (apps/api/app/llm.py). story_breakdown is left out
-- of the seeded set here — it has no live call site to seed content for yet.
insert into public.project_template_sections (template_id, section_type, section_key, content, sort_order)
select t.id, 'prompt', 'test_case_elaborate', $tmpl$You write manual test cases for a software product.

Expand the rough test description below into a concrete manual test case.
Respond with ONLY a JSON object, no prose, no code fences:
{"title": "<concise test title>",
  "steps": "<numbered markdown steps a human tester follows>",
  "expected_result": "<what the tester should observe when it passes>"}

Rough description:
{description}
{context_block}$tmpl$, 1
from public.project_templates t where t.key = 'default';

insert into public.project_template_sections (template_id, section_type, section_key, content, sort_order)
select t.id, 'prompt', 'deploy_script_generate', $tmpl$You write POSIX shell deployment scripts for a software factory that ships a project's files to a server, then runs the script.

Execution contract (the script MUST fit this — do not invent a different runner):
- Invoked as `sh -e` (errexit) with stdin = the script body.
- Working directory is the deploy target folder (or the new release folder when strategy is "releases").
- In releases mode these are already exported: SF_RELEASE_PATH (this release's directory) and SF_TARGET (the long-lived target folder). Long-lived config/symlinks should point at `$SF_TARGET/current`, not a specific release path.
- The following environment variable NAMES (values are injected at run time — never invent secrets) are exported before your script runs: {env_var_names}.
- Prefer portable POSIX `sh`. No interactive prompts. Fail fast on errors.

Respond with ONLY the shell script body — no markdown fences, no prose before or after.

Project overview:
- Name: {project_name}
- Description: {project_description}
- Repo: {repo_full_name}
- Default branch: {default_branch}

Project guidelines:
{guidelines}

Deployment configuration (in-form draft — may be unsaved):
- Name: {deployment_name}
- Branch: {branch}
- Target folder: {target_folder}
- Source folder (repo subfolder, empty = whole repo): {source_folder}
- Strategy: {strategy}
- Keep releases: {keep_releases}
- Run timeout (minutes): {run_timeout_minutes}
- Health check URL (optional): {health_check_url}
$tmpl$, 2
from public.project_templates t where t.key = 'default';

-- ---------------------------------------------------------------------------
-- Seed every org with a copy of the Default template
-- ---------------------------------------------------------------------------
create or replace function public.seed_org_default_project_template(p_org uuid)
returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
  v_default_id uuid;
  v_org_template_id uuid;
begin
  select id into v_org_template_id
  from public.org_project_templates
  where org_id = p_org and is_default
  limit 1;
  if v_org_template_id is not null then
    return v_org_template_id;
  end if;

  select id into v_default_id from public.project_templates where key = 'default';
  if v_default_id is null then
    return null;
  end if;

  insert into public.org_project_templates
    (org_id, template_key, seeded_version, name, description, is_default, sort_order)
  select p_org, t.key, t.version, t.name, t.description, true, t.sort_order
  from public.project_templates t
  where t.id = v_default_id
  returning id into v_org_template_id;

  insert into public.org_project_template_sections
    (org_template_id, org_id, section_type, section_key, title, content, sort_order)
  select v_org_template_id, p_org, s.section_type, s.section_key, s.title, s.content, s.sort_order
  from public.project_template_sections s
  where s.template_id = v_default_id;

  return v_org_template_id;
end;
$$;

revoke all on function public.seed_org_default_project_template(uuid) from public, anon, authenticated;

create or replace function public.seed_default_project_template_on_new_org()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  perform public.seed_org_default_project_template(new.id);
  return new;
end;
$$;

create trigger organizations_seed_default_project_template
  after insert on public.organizations
  for each row execute function public.seed_default_project_template_on_new_org();

-- Every existing org gets the Default copy.
select public.seed_org_default_project_template(id) from public.organizations;

-- ---------------------------------------------------------------------------
-- A new project silently inherits the org's default template — no picker.
-- ---------------------------------------------------------------------------
create or replace function public.default_project_org_template()
returns trigger
language plpgsql
as $$
begin
  if new.org_template_id is null then
    select id into new.org_template_id
    from public.org_project_templates
    where org_id = new.org_id and is_default
    limit 1;
  end if;
  return new;
end;
$$;

create trigger projects_default_org_template
  before insert on public.projects
  for each row execute function public.default_project_org_template();

-- Backfill provenance on every existing project — content is left exactly
-- as-is; only the link is added, so nothing changes behaviorally.
update public.projects p
set org_template_id = opt.id
from public.org_project_templates opt
where opt.org_id = p.org_id
  and opt.is_default
  and p.org_template_id is null;

-- ---------------------------------------------------------------------------
-- Seeding triggers now read the project's chosen template first
-- ---------------------------------------------------------------------------
create or replace function public.seed_worker_instructions()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into public.worker_instructions (org_id, project_id, run_kind, content)
  select
    new.org_id, new.id, k.kind,
    coalesce(
      (
        select s.content from public.org_project_template_sections s
        where s.org_template_id = new.org_template_id
          and s.section_type = 'worker_instruction'
          and s.section_key = k.kind
      ),
      public.default_worker_instruction(k.kind),
      ''
    )
  from (values
    ('prd'), ('plan'), ('code'), ('release'), ('breakdown'), ('test'),
    ('deploy'), ('guidelines'), ('elaborate'), ('wireframe')
  ) as k(kind)
  on conflict (project_id, run_kind) do nothing;

  -- The two project-shaped thinking prompts, seeded from the template's
  -- 'prompt' sections when present (else left blank — resolve_prompt falls
  -- back to the global override / LLM_FUNCTIONS default).
  insert into public.worker_instructions (org_id, project_id, run_kind, content)
  select
    new.org_id, new.id, k.kind,
    coalesce(
      (
        select s.content from public.org_project_template_sections s
        where s.org_template_id = new.org_template_id
          and s.section_type = 'prompt'
          and s.section_key = k.kind
      ),
      ''
    )
  from (values ('test_case_elaborate'), ('deploy_script_generate')) as k(kind)
  on conflict (project_id, run_kind) do nothing;

  return new;
end;
$$;

-- Backfill the two new kinds for every existing project — same content a
-- fresh project under its own org_template_id would get.
insert into public.worker_instructions (org_id, project_id, run_kind, content)
select
  p.org_id, p.id, k.kind,
  coalesce(
    (
      select s.content from public.org_project_template_sections s
      where s.org_template_id = p.org_template_id
        and s.section_type = 'prompt'
        and s.section_key = k.kind
    ),
    ''
  )
from public.projects p
cross join (values ('test_case_elaborate'), ('deploy_script_generate')) as k(kind)
on conflict (project_id, run_kind) do nothing;

create or replace function public.seed_buildmill_guidelines_section()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  v_content text;
begin
  select s.content into v_content
  from public.org_project_template_sections s
  where s.org_template_id = new.org_template_id
    and s.section_type = 'guideline'
    and s.section_key = 'buildmill-workflow';

  insert into public.project_guidelines
    (org_id, project_id, section_key, title, content, sort_order)
  values
    (new.org_id, new.id, 'buildmill-workflow', 'Working with Build Mill',
     coalesce(v_content, public.effective_guideline_section('buildmill-workflow')), 999)
  on conflict (project_id, section_key) where section_key <> 'custom'
  do nothing;
  return new;
end;
$$;

create or replace function public.seed_release_guidelines_section()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  v_content text;
begin
  select s.content into v_content
  from public.org_project_template_sections s
  where s.org_template_id = new.org_template_id
    and s.section_type = 'guideline'
    and s.section_key = 'release';

  insert into public.project_guidelines
    (org_id, project_id, section_key, title, content, sort_order)
  values
    (new.org_id, new.id, 'release', 'Versioning & Release',
     coalesce(v_content, public.default_guidelines_release_section()), 998)
  on conflict (project_id, section_key) where section_key <> 'custom'
  do nothing;
  return new;
end;
$$;
