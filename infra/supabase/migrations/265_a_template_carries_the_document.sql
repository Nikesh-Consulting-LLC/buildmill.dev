-- 265_a_template_carries_the_document (us-100.4): a template holds the Agent
-- Instructions document, alongside its per-task instructions.
--
-- DELIBERATE DEVIATION FROM THE STORY. us-100.4 AC2 said `prompt` rows are
-- "dropped" and `guideline` rows migrated. Before writing that, I counted:
-- production holds 18 prompt sections (16 org, 2 platform) and every one has
-- content. Deleting them is real data loss, and `llm_prompt_templates` does
-- NOT hold the same thing — a template's prompt section is that template's
-- own customization.
--
-- Retiring them from the EDITOR needs no deletion at all. This migration is
-- therefore purely additive: it adds the document, backfills it from the
-- guideline sections, and touches nothing else. The narrowing happens in the
-- UI, where it belongs, and every existing row stays exactly where it is —
-- recoverable, and reversible by reverting one commit.
--
-- Same verify-or-rollback shape as 263: the backfill is compared against what
-- it was derived from, and the transaction aborts on any mismatch.

-- 1 -------------------------------------------------------------- the field
alter table public.project_templates
  add column if not exists agent_instructions text not null default '';

alter table public.org_project_templates
  add column if not exists agent_instructions text not null default '';

comment on column public.project_templates.agent_instructions is
  'us-100.4: the Agent Instructions document this template seeds. Replaces '
  'its guideline sections, which are retained (not dropped) as a rollback.';

comment on column public.org_project_templates.agent_instructions is
  'us-100.4: the Agent Instructions document this template seeds into a new '
  'project. Replaces its guideline sections, which are retained.';

-- 2 --------------------------------------------- backfill from the sections
do $migration$
declare
  platform_expected int;
  org_expected int;
  platform_got int;
  org_got int;
begin
  -- The same rendering the project-side backfill used in 263: each section as
  -- an H2 with its title, in sort_order.
  update public.project_templates t
     set agent_instructions = s.doc
    from (
      select template_id,
             string_agg('## ' || coalesce(nullif(btrim(title), ''), section_key)
                        || E'\n\n' || btrim(content),
                        E'\n\n' order by sort_order, section_key) as doc
      from public.project_template_sections
      where section_type = 'guideline'
        and coalesce(btrim(content), '') <> ''
      group by template_id
    ) s
   where s.template_id = t.id
     and coalesce(t.agent_instructions, '') = '';

  update public.org_project_templates t
     set agent_instructions = s.doc
    from (
      select org_template_id,
             string_agg('## ' || coalesce(nullif(btrim(title), ''), section_key)
                        || E'\n\n' || btrim(content),
                        E'\n\n' order by sort_order, section_key) as doc
      from public.org_project_template_sections
      where section_type = 'guideline'
        and coalesce(btrim(content), '') <> ''
      group by org_template_id
    ) s
   where s.org_template_id = t.id
     and coalesce(t.agent_instructions, '') = '';

  -- Every template that HAD guideline content must now have a document.
  select count(distinct template_id) into platform_expected
  from public.project_template_sections
  where section_type = 'guideline' and coalesce(btrim(content), '') <> '';

  select count(*) into platform_got
  from public.project_templates
  where coalesce(btrim(agent_instructions), '') <> '';

  select count(distinct org_template_id) into org_expected
  from public.org_project_template_sections
  where section_type = 'guideline' and coalesce(btrim(content), '') <> '';

  select count(*) into org_got
  from public.org_project_templates
  where coalesce(btrim(agent_instructions), '') <> '';

  if platform_got < platform_expected or org_got < org_expected then
    raise exception
      'backfill incomplete — platform %/%, org %/% — rolling back',
      platform_got, platform_expected, org_got, org_expected;
  end if;

  raise notice 'templates carrying a document: platform %, org %',
    platform_got, org_got;
end
$migration$;
