-- 285_existing_templates_get_a_face (Phase 118): the templates that already
-- exist match the new design — a distinct short markdown description, a
-- category, and a cover — instead of arriving on the new cards as three
-- names sharing one boilerplate sentence and a monogram.
--
-- Data only; migration 284 holds the schema. Idempotent and conservative:
--   * a description is replaced only while it still holds the exact
--     boilerplate every template was created with — anything an admin has
--     already written is left alone;
--   * a cover is set only where none is set;
--   * rows are matched by key AND name, so a later duplicate that happens to
--     reuse an auto-generated key is not rewritten;
--   * org copies inherit the catalog's face on the same terms, and only
--     where they were copied from that catalog row (template_key).
-- Covers are the built-in set shipped with the web app (`builtin/<name>`),
-- so this runs the same on both projects with no Storage upload.

do $$
declare
  boilerplate constant text :=
    'The factory''s baked-in starting point, drafted from today''s factory defaults so nothing changes for an existing project.';
begin
  -- ---------------------------------------------------------------- catalog

  update public.project_templates
     set description = 'The factory''s baked-in starting point — today''s defaults, unchanged. Pick this when nothing else fits.',
         category    = case when category = '' then 'General' else category end
   where key = 'default'
     and description = boilerplate;

  update public.project_templates
     set image_path = 'builtin/factory'
   where key = 'default'
     and image_path is null;

  update public.project_templates
     set description = 'A starting point for a browser-facing app — a **frontend and an API** in one repo.'
   where key = 'default-copy-2'
     and name = 'Generic Web App'
     and description = boilerplate;

  update public.project_templates
     set image_path = 'builtin/web-app'
   where key = 'default-copy-2'
     and name = 'Generic Web App'
     and image_path is null;

  update public.project_templates
     set description = 'A starting point for a **FastAPI** backend with a **Next.js** frontend.'
   where key = 'default-copy'
     and name = 'Python + Next.JS Web App'
     and description = boilerplate;

  update public.project_templates
     set image_path = 'builtin/full-stack'
   where key = 'default-copy'
     and name = 'Python + Next.JS Web App'
     and image_path is null;

  -- ---------------------------------------------------------------- org copies

  update public.org_project_templates o
     set description = t.description
    from public.project_templates t
   where o.template_key = t.key
     and o.description = boilerplate
     and t.description <> boilerplate;

  update public.org_project_templates o
     set image_path = t.image_path
    from public.project_templates t
   where o.template_key = t.key
     and o.image_path is null
     and t.image_path is not null;

  update public.org_project_templates o
     set category = t.category
    from public.project_templates t
   where o.template_key = t.key
     and o.category = ''
     and t.category <> '';
end $$;
