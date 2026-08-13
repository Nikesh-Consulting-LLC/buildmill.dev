-- 084_guidelines_dedupe_heading: stop doubling section headings in the
-- assembled guidelines (AGENTS.md export, download, and dispatched work
-- context all go through assemble_project_guidelines).
--
-- The renderer prepends '## <title>' to every section. But some section
-- *contents* already begin with their own '## <title>' line — the us-7.8 AI
-- setup brainstorm drafted sections (Run commands, Tech stack, Versioning &
-- Release) that way. Those sections then rendered the heading twice in the
-- committed AGENTS.md. Sections whose content leads with prose (Project
-- overview, Working with Build Mill) were unaffected — which is exactly the
-- pattern the user hit.
--
-- Fix: when a section's content already leads with an ATX heading whose text
-- equals the section title (case-insensitive), strip that leading heading line
-- before the renderer supplies its own. Heading level is normalized to the
-- renderer's '## ' either way. No signature change, so no type regen.

create or replace function public.assemble_project_guidelines(p_project uuid)
returns text
language sql
stable
as $$
  select trim(both E'\n' from concat_ws(E'\n\n',
    coalesce((
      select string_agg(
        '## ' || g.title || E'\n\n' || g.body,
        E'\n\n' order by g.sort_order, g.created_at)
      from (
        select
          title,
          sort_order,
          created_at,
          case
            -- First line is an ATX heading (#..######) whose text matches the
            -- section title → drop it; the renderer adds the '## title' itself.
            when lower(trim(coalesce(
                   substring(trim(content) from '^#{1,6}[ \t]+([^\r\n]*)'), '')))
                 = lower(trim(title))
            then regexp_replace(
                   trim(content), '^#{1,6}[ \t]+[^\r\n]*((\r?\n)+|$)', '')
            else trim(content)
          end as body
        from public.project_guidelines
        where project_id = p_project
          and length(trim(content)) > 0
      ) g), ''),
    case when public.project_environment_md(p_project) is not null
      then '## Environment (runtime & setup)' || E'\n\n'
           || public.project_environment_md(p_project) end
  ));
$$;
