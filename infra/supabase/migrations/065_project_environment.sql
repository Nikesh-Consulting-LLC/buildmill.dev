-- 065_project_environment: structured environment declaration (US-5.23).
-- The project declares its toolchain — runtime/version, ordered setup
-- commands, free-text notes — so a worker's "verify your work" is
-- mechanical instead of interpretive archaeology across prose
-- guidelines. Declared context only; no provisioning.

alter table public.projects
  add column env_runtime text not null default '',
  add column env_setup_commands jsonb not null default '[]'::jsonb,
  add column env_notes text not null default '';

-- One renderer for every surface (work context, AGENTS.md export):
-- NULL when nothing is configured, so empty stays absent — matching
-- the us-5.9 run-commands behavior.
create or replace function public.project_environment_md(p_project uuid)
returns text
language sql
stable
as $$
  select nullif(concat_ws(E'\n\n',
    case when trim(coalesce(p.env_runtime, '')) <> ''
      then '- Runtime: ' || trim(p.env_runtime) end,
    case when jsonb_array_length(coalesce(p.env_setup_commands, '[]'::jsonb)) > 0
      then 'Setup, in order:' || E'\n' || (
        select string_agg('- `' || trim(t.cmd) || '`', E'\n')
        from jsonb_array_elements_text(p.env_setup_commands) as t(cmd)
        where trim(t.cmd) <> '')
      end,
    nullif(trim(coalesce(p.env_notes, '')), '')
  ), '')
  from public.projects p
  where p.id = p_project;
$$;

-- The AGENTS.md export (and dispatched guidelines, same function)
-- includes the environment when present — the in-repo and over-MCP
-- stories stay consistent. Titled to avoid colliding with the
-- catalog's free-text 'environment' section.
create or replace function public.assemble_project_guidelines(p_project uuid)
returns text
language sql
stable
as $$
  select trim(both E'\n' from concat_ws(E'\n\n',
    coalesce((
      select string_agg(
        '## ' || title || E'\n\n' || content,
        E'\n\n' order by sort_order, created_at)
      from public.project_guidelines
      where project_id = p_project
        and length(trim(content)) > 0), ''),
    case when public.project_environment_md(p_project) is not null
      then '## Environment (runtime & setup)' || E'\n\n'
           || public.project_environment_md(p_project) end
  ));
$$;
