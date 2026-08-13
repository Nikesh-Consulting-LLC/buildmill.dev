-- 228_project_template_advisor_fixes: tighten two functions the security
-- advisor flagged after 227 (us-67.1) — a mutable search_path on the
-- version-bump trigger, and no revoke on the new-org seeding trigger
-- function (the precedent, seed_presets_on_new_org in 157, has the same gap;
-- fixing both properly here rather than copying it forward).

create or replace function public.bump_project_template_version()
returns trigger
language plpgsql
set search_path = public
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

revoke all on function public.seed_default_project_template_on_new_org() from public, anon, authenticated;
