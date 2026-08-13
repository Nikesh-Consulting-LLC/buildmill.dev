-- 229_project_template_disable: a superadmin can disable a project template
-- (Phase 67 follow-up) — hidden from every org's read (direct Supabase and
-- the settings catalog), still visible/editable to the superadmin so it can
-- be re-enabled. The default template can never be disabled: every project
-- without an explicit template falls back to it, so hiding it would silently
-- orphan every future project creation.

alter table public.project_templates
  add column is_disabled boolean not null default false;

drop policy if exists "any member reads project templates" on public.project_templates;

create policy "members read enabled project templates, admin reads all"
  on public.project_templates for select
  to authenticated
  using (not is_disabled or public.is_platform_admin());
