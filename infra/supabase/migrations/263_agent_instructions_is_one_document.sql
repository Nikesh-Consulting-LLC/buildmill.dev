-- 263_agent_instructions_is_one_document (us-100.1): the twenty-two-section
-- guidelines catalog becomes one markdown document.
--
-- The structure bought nothing. An agent has always received the sections
-- concatenated back into a single document by assemble_project_guidelines,
-- so the catalog only ever existed on the way in — a form with twenty-two
-- boxes where a person wants an editor, and no single place a human could
-- read what an agent actually gets.
--
-- SAFETY: this migration is additive and reversible. It adds the column,
-- fills it with EXACTLY what assemble_project_guidelines returns today, and
-- only then repoints that function at the new field. public.project_guidelines
-- is left completely intact — us-100.1 AC7 requires its drop to be a separate,
-- later migration so this backfill can be verified on live data first.
--
-- The verification that matters is in the DO block: every project's new
-- document must equal what the function returned before the switch, or the
-- transaction rolls back. A silent difference here is a silent change to
-- what every agent in the factory reads.

-- 1 -------------------------------------------------------------- the field
alter table public.projects
  add column if not exists agent_instructions text not null default '';

comment on column public.projects.agent_instructions is
  'us-100.1: the project''s Agent Instructions — one markdown document, and '
  'the body of AGENTS.md (us-100.2). Replaces the twenty-two-key '
  'project_guidelines catalog, which is retained for one release as a '
  'rollback and dropped by a later migration.';

-- 2 ------------------------------------------------- backfill, then prove it
do $migration$
declare
  before_state jsonb;
  mismatches int;
begin
  -- What every project's agent reads RIGHT NOW, captured before anything moves.
  select coalesce(jsonb_object_agg(id::text, coalesce(doc, '')), '{}'::jsonb)
    into before_state
  from (
    select p.id, public.assemble_project_guidelines(p.id) as doc
    from public.projects p
  ) s;

  update public.projects p
     set agent_instructions = coalesce(before_state ->> p.id::text, '')
   where coalesce(before_state ->> p.id::text, '') <> ''
     and coalesce(p.agent_instructions, '') = '';

  -- Every project's stored document must equal what it was serving.
  select count(*) into mismatches
  from public.projects p
  where coalesce(p.agent_instructions, '')
        is distinct from coalesce(before_state ->> p.id::text, '');

  if mismatches > 0 then
    raise exception
      'backfill lost content on % project(s) — rolling back rather than '
      'changing what agents read', mismatches;
  end if;

  raise notice 'agent_instructions backfilled and verified for % project(s)',
    (select count(*) from jsonb_object_keys(before_state));
end
$migration$;

-- 3 ------------------------------------- the assembler reads the field now
--
-- Deliberately keeping the NAME and the signature. us-100.1 AC4: dispatch_issue
-- still writes input_context.guidelines from this, and every MCP tool and
-- runner path that reads it keeps working untouched. The vocabulary change is
-- the manager's (us-100.3), not the wire format's.
create or replace function public.assemble_project_guidelines(p_project uuid)
returns text
language sql
stable
as $function$
  select coalesce(agent_instructions, '')
  from public.projects
  where id = p_project;
$function$;

comment on function public.assemble_project_guidelines(uuid) is
  'us-100.1: now a thin read of projects.agent_instructions. Kept under its '
  'old name and signature so dispatch_issue, the MCP tools and the runner '
  'need no change — project_guidelines is no longer read by anything.';
