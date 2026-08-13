-- 042_org_project_slugs: US-3.13 — human identifiers for git URLs.
-- organizations.shortname: lowercase slug, <=24 chars, unique system-wide.
-- projects.slug: lowercase slug, unique within its org.
-- Both auto-generate from the row's name on insert (trigger), collision
-- gets a numeric suffix. Stable once minted: nothing regenerates them on
-- rename, so issued clone URLs never break.

-- One sanitization pipeline for both identifiers.
create or replace function public.slugify(input text, max_len int default null)
returns text
language plpgsql
immutable
set search_path = public
as $$
declare
  s text;
begin
  s := lower(coalesce(input, ''));
  s := regexp_replace(s, '[^a-z0-9]+', '-', 'g');
  s := btrim(s, '-');
  if max_len is not null and length(s) > max_len then
    s := btrim(left(s, max_len), '-');
  end if;
  if s = '' then
    s := 'x';
  end if;
  return s;
end $$;

-- security definer: global uniqueness needs to see every org's shortname,
-- which RLS would hide from the inserting user. Returns only the candidate
-- string — no row data escapes.
create or replace function public.next_org_shortname(p_name text)
returns text
language plpgsql
security definer
set search_path = public
as $$
declare
  base text := public.slugify(p_name, 24);
  candidate text := base;
  n int := 1;
begin
  while exists (select 1 from public.organizations where shortname = candidate) loop
    n := n + 1;
    candidate := btrim(left(base, 24 - length(n::text) - 1), '-') || '-' || n;
  end loop;
  return candidate;
end $$;

create or replace function public.next_project_slug(p_org uuid, p_name text)
returns text
language plpgsql
security definer
set search_path = public
as $$
declare
  base text := public.slugify(p_name);
  candidate text := base;
  n int := 1;
begin
  while exists (
    select 1 from public.projects where org_id = p_org and slug = candidate
  ) loop
    n := n + 1;
    candidate := base || '-' || n;
  end loop;
  return candidate;
end $$;

alter table public.organizations add column shortname text;
alter table public.projects add column slug text;

-- Backfill existing rows oldest-first so suffix numbering is deterministic.
do $$
declare r record;
begin
  for r in
    select id, name from public.organizations
    where shortname is null order by created_at, id
  loop
    update public.organizations
      set shortname = public.next_org_shortname(r.name)
      where id = r.id;
  end loop;
end $$;

do $$
declare r record;
begin
  for r in
    select id, org_id, name from public.projects
    where slug is null order by created_at, id
  loop
    update public.projects
      set slug = public.next_project_slug(r.org_id, r.name)
      where id = r.id;
  end loop;
end $$;

alter table public.organizations
  alter column shortname set not null,
  add constraint organizations_shortname_key unique (shortname),
  add constraint organizations_shortname_format
    check (shortname ~ '^[a-z0-9](-?[a-z0-9])*$' and char_length(shortname) <= 24);

alter table public.projects
  alter column slug set not null,
  add constraint projects_slug_format check (slug ~ '^[a-z0-9](-?[a-z0-9])*$');

create unique index projects_org_slug_key on public.projects (org_id, slug);

-- Auto-generate on insert wherever the row is created (signup trigger,
-- superadmin console, project dialog) — no creation path can forget.
create or replace function public.set_org_shortname()
returns trigger
language plpgsql
set search_path = public
as $$
begin
  if new.shortname is null then
    new.shortname := public.next_org_shortname(new.name);
  end if;
  return new;
end $$;

create trigger organizations_set_shortname
  before insert on public.organizations
  for each row execute function public.set_org_shortname();

create or replace function public.set_project_slug()
returns trigger
language plpgsql
set search_path = public
as $$
begin
  if new.slug is null then
    new.slug := public.next_project_slug(new.org_id, new.name);
  end if;
  return new;
end $$;

create trigger projects_set_slug
  before insert on public.projects
  for each row execute function public.set_project_slug();
