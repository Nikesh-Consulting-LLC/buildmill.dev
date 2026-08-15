-- 266_a_new_project_inherits_the_document (us-100.4 AC3): a project created
-- from a template starts with that template's Agent Instructions.
--
-- Extends the existing seeding trigger rather than adding a second one: the
-- per-task instructions and the document are one act of seeding, and two
-- triggers on the same insert would be two things to keep in step.
--
-- An UPDATE rather than a field assignment because this is an AFTER trigger
-- (it inserts worker_instructions rows). A project with no template, or a
-- template with no document, keeps the empty default — not an error. The
-- `p.agent_instructions = ''` guard means a create that supplies its own
-- document is never overwritten by the template's.
--
-- (Body identical to what was applied to both databases; see git history for
-- the full function — it is the 261 seeder plus the final UPDATE.)

create or replace function public.seed_worker_instructions()
returns trigger
language plpgsql
security definer
set search_path = public
as $function$
begin
  insert into public.worker_instructions (org_id, project_id, run_kind, content)
  select
    new.org_id, new.id, k.kind,
    coalesce(
      (
        select s.content from public.org_project_template_sections s
        where s.org_template_id = new.org_template_id
          and s.section_type = 'worker_instruction'
          and s.section_key = k.kind
      ),
      public.default_worker_instruction(k.kind),
      ''
    )
  from (values
    ('prd'), ('plan'), ('code'), ('release'), ('breakdown'), ('test'),
    ('deploy'), ('guidelines'), ('elaborate'), ('wireframe'), ('chore'),
    ('bug_rca'), ('bug_fix'), ('standalone_plan'), ('standalone_code'),
    ('merge')
  ) as k(kind)
  on conflict (project_id, run_kind) do nothing;

  insert into public.worker_instructions (org_id, project_id, run_kind, content)
  select
    new.org_id, new.id, k.kind,
    coalesce(
      (
        select s.content from public.org_project_template_sections s
        where s.org_template_id = new.org_template_id
          and s.section_type = 'prompt'
          and s.section_key = k.kind
      ),
      ''
    )
  from (values ('test_case_elaborate'), ('deploy_script_generate')) as k(kind)
  on conflict (project_id, run_kind) do nothing;

  -- us-100.4 AC3: the Agent Instructions document comes from the template too.
  update public.projects p
     set agent_instructions = t.agent_instructions
    from public.org_project_templates t
   where p.id = new.id
     and t.id = new.org_template_id
     and coalesce(btrim(t.agent_instructions), '') <> ''
     and coalesce(btrim(p.agent_instructions), '') = '';

  return new;
end;
$function$;
