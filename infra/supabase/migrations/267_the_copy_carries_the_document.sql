-- 267_the_copy_carries_the_document (us-100.4 AC3): copying a catalog
-- template into an org carries its Agent Instructions document, and only the
-- files.
--
-- Migration 265 gave both template tables an `agent_instructions` column and
-- 266 taught project creation to read the org copy's. The step between them
-- — copy_project_template_into_org, the "add this template to my org" action
-- — still copied the row without the column, so a freshly copied template
-- arrived with an empty document even when the catalog original had one.
--
-- The section copy narrows to `worker_instruction`: the retired
-- `guideline`/`prompt` rows on a catalog template are rollback data
-- (migration 265 deletes nothing), not content a new org copy should inherit.

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
    (org_id, template_key, seeded_version, name, description, sort_order,
     agent_instructions)
  values
    (p_org, v_template.key, v_template.version, p_name, v_template.description,
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
