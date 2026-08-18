-- 284_a_template_carries_a_cover (US-118.1): a template gets a face.
--
-- Both template tables gain `image_path`; the org copy also gains the
-- `category` it was missing (the catalog had one since 227, write-only). A
-- new public bucket, `template-images`, holds uploaded covers, written from
-- the browser under RLS: the platform admin under `catalog/<template_id>/cover`,
-- an org manager under `<org_id>/<org_template_id>/cover`. A cover holds no
-- secrets — that is what makes a public bucket acceptable here where it is
-- not for `attachments`: a row of twenty cards is twenty plain <img> tags.
--
-- `image_path` has three shapes and one meaning each:
--   null                          no image; the web renders a generated cover
--                                 (initials on a tint) — a real, good state
--   builtin/<name>                one of the covers shipped with the web app
--                                 (apps/web/public/template-covers/<name>.svg)
--   catalog/<uuid>/cover          an upload on the catalog row (an org copy
--                                 may inherit this path from the catalog)
--   <org_id>/<own id>/cover       the org's own upload (org copies only)
--
-- Object paths are fixed and extension-less, like avatars (014), so replace
-- is an upsert and nothing is orphaned. Extension of the CHECKs is the only
-- way a fourth shape ever appears.

-- ---------------------------------------------------------------- columns

alter table public.project_templates
  add column if not exists image_path text;

alter table public.project_templates
  drop constraint if exists project_templates_image_path_check;
alter table public.project_templates
  add constraint project_templates_image_path_check check (
    image_path is null
    or image_path ~ '^builtin/[a-z0-9-]{1,40}$'
    or image_path = 'catalog/' || id::text || '/cover'
  );

alter table public.org_project_templates
  add column if not exists category text not null default '',
  add column if not exists image_path text;

alter table public.org_project_templates
  drop constraint if exists org_project_templates_image_path_check;
alter table public.org_project_templates
  add constraint org_project_templates_image_path_check check (
    image_path is null
    or image_path ~ '^builtin/[a-z0-9-]{1,40}$'
    or image_path ~ '^catalog/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/cover$'
    or image_path = org_id::text || '/' || id::text || '/cover'
  );

-- An org copy made from the catalog inherits the catalog's category (the
-- column did not exist when it was copied). Custom templates keep ''.
update public.org_project_templates o
   set category = t.category
  from public.project_templates t
 where o.template_key = t.key
   and o.category = ''
   and t.category <> '';

-- ---------------------------------------------------------------- copy RPC

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
    (org_id, template_key, seeded_version, name, description, category, image_path,
     sort_order, agent_instructions)
  values
    (p_org, v_template.key, v_template.version, p_name, v_template.description,
     v_template.category, v_template.image_path,
     v_template.sort_order, coalesce(v_template.agent_instructions, ''))
  returning id into v_org_template_id;

  insert into public.org_project_template_sections
    (org_template_id, org_id, section_type, section_key, title, content, sort_order)
  select v_org_template_id, p_org, s.section_type, s.section_key, s.title, s.content, s.sort_order
  from public.project_template_sections s
  where s.template_id = p_template_id
    and s.section_type = 'worker_instruction';

  return v_org_template_id;
end;
$$;

revoke all on function public.copy_project_template_into_org(uuid, uuid, text) from public, anon;
grant execute on function public.copy_project_template_into_org(uuid, uuid, text) to authenticated;

-- ---------------------------------------------------------------- bucket

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
  'template-images',
  'template-images',
  true,
  2097152,
  array['image/png', 'image/jpeg', 'image/webp', 'image/gif', 'image/svg+xml']
)
on conflict (id) do nothing;

-- Who may write which object. One decision for the three write policies:
--   catalog/<uuid>/cover   → the platform admin
--   <org uuid>/<uuid>/cover → a manager of that org
--   anything else          → false, without a cast error on a stray folder
create or replace function public.template_image_writable(p_name text)
returns boolean
language sql
stable
set search_path = public
as $$
  -- CASE evaluates in order, so the uuid cast is only reached for a name that
  -- already matched the shape (AND alone does not promise short-circuit).
  select case
    when p_name !~ '^(catalog|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/cover$'
      then false
    when split_part(p_name, '/', 1) = 'catalog'
      then public.is_platform_admin()
    else public.has_org_capability(split_part(p_name, '/', 1)::uuid, 'manage_project')
  end;
$$;

revoke all on function public.template_image_writable(text) from public, anon;
grant execute on function public.template_image_writable(text) to authenticated;

drop policy if exists "template images are publicly readable" on storage.objects;
create policy "template images are publicly readable"
  on storage.objects for select
  using (bucket_id = 'template-images');

drop policy if exists "template covers: admins and managers upload" on storage.objects;
create policy "template covers: admins and managers upload"
  on storage.objects for insert
  to authenticated
  with check (bucket_id = 'template-images' and public.template_image_writable(name));

drop policy if exists "template covers: admins and managers replace" on storage.objects;
create policy "template covers: admins and managers replace"
  on storage.objects for update
  to authenticated
  using (bucket_id = 'template-images' and public.template_image_writable(name))
  with check (bucket_id = 'template-images' and public.template_image_writable(name));

drop policy if exists "template covers: admins and managers remove" on storage.objects;
create policy "template covers: admins and managers remove"
  on storage.objects for delete
  to authenticated
  using (bucket_id = 'template-images' and public.template_image_writable(name));
