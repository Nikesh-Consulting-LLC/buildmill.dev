-- 270_the_document_is_audited (us-100.1 AC3, closing a gap the Phase 100
-- close found): edits to a project's Agent Instructions land in
-- content_audit.
--
-- The `project` branch of record_content_audit audits a fixed list of
-- columns; `agent_instructions` (migration 263) was not on it, so since the
-- section editor was replaced by the document editor no edit to the
-- conventions has been recorded — the History link on the tab answered
-- nothing new, and "edited since marked ready" had nothing to read.
--
-- An edit to the document is recorded under surface `guidelines` (the
-- surface History already filters on, and the one us-100.3 deliberately kept
-- as a storage key) with item_key `AGENTS.md`, before/after the whole text.
-- Rebuilt from the live body (verified identical on both projects, including
-- 225's org-delete guard); the only change is the extra branch.

create or replace function public.record_content_audit()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  v_surface text := tg_argv[0];
  a record;
  f record;
  v_key text;
begin
  if coalesce(current_setting('app.org_being_deleted', true), '') = 'on' then
    return coalesce(new, old);
  end if;

  select * into a from public.content_audit_actor();

  if v_surface = 'project' then
    if tg_op = 'INSERT' then
      insert into public.content_audit
        (org_id, project_id, surface, item_key, action,
         actor_type, actor_id, actor_name, before_text, after_text)
      values
        (new.org_id, new.id, 'project', 'project', 'created',
         a.actor_type, a.actor_id, a.actor_name, null, new.name);
    else
      for f in
        select * from (values
          ('name', old.name, new.name),
          ('description', old.description, new.description),
          ('repo_full_name', old.repo_full_name, new.repo_full_name),
          ('default_branch', old.default_branch, new.default_branch),
          ('env_runtime', old.env_runtime, new.env_runtime),
          ('env_setup_commands',
            old.env_setup_commands::text, new.env_setup_commands::text),
          ('env_notes', old.env_notes, new.env_notes)
        ) as t(key, before, after)
        where coalesce(t.before, '') is distinct from coalesce(t.after, '')
      loop
        insert into public.content_audit
          (org_id, project_id, surface, item_key, action,
           actor_type, actor_id, actor_name, before_text, after_text)
        values
          (new.org_id, new.id, 'project', f.key, 'updated',
           a.actor_type, a.actor_id, a.actor_name, f.before, f.after);
      end loop;
      -- us-100.1 AC3 / 270: the Agent Instructions document, under the
      -- surface History reads and "edited since ready" checks.
      if coalesce(old.agent_instructions, '')
         is distinct from coalesce(new.agent_instructions, '') then
        insert into public.content_audit
          (org_id, project_id, surface, item_key, action,
           actor_type, actor_id, actor_name, before_text, after_text)
        values
          (new.org_id, new.id, 'guidelines', 'AGENTS.md', 'updated',
           a.actor_type, a.actor_id, a.actor_name,
           old.agent_instructions, new.agent_instructions);
      end if;
    end if;
    return coalesce(new, old);
  end if;

  if v_surface = 'guidelines' then
    v_key := coalesce(new.title, old.title, '');
  elsif v_surface = 'worker_instructions' then
    v_key := coalesce(new.run_kind, old.run_kind, '');
  else
    v_key := 'learnings';
  end if;

  if tg_op = 'INSERT' then
    insert into public.content_audit
      (org_id, project_id, surface, item_key, action,
       actor_type, actor_id, actor_name, before_text, after_text)
    values
      (new.org_id, new.project_id, v_surface, v_key, 'created',
       a.actor_type, a.actor_id, a.actor_name, null, new.content);
  elsif tg_op = 'DELETE' then
    insert into public.content_audit
      (org_id, project_id, surface, item_key, action,
       actor_type, actor_id, actor_name, before_text, after_text)
    values
      (old.org_id, old.project_id, v_surface, v_key, 'deleted',
       a.actor_type, a.actor_id, a.actor_name, old.content, null);
  elsif v_surface = 'guidelines' then
    if old.content is distinct from new.content
       or old.title is distinct from new.title
    then
      insert into public.content_audit
        (org_id, project_id, surface, item_key, action,
         actor_type, actor_id, actor_name, before_text, after_text)
      values
        (new.org_id, new.project_id, 'guidelines',
         case
           when old.title is distinct from new.title
             then old.title || ' → ' || new.title
           else v_key
         end,
         'updated', a.actor_type, a.actor_id, a.actor_name,
         old.content, new.content);
    end if;
    if old.sort_order is distinct from new.sort_order then
      insert into public.content_audit
        (org_id, project_id, surface, item_key, action,
         actor_type, actor_id, actor_name, before_text, after_text)
      values
        (new.org_id, new.project_id, 'guidelines', v_key, 'updated',
         a.actor_type, a.actor_id, a.actor_name,
         'position ' || old.sort_order, 'position ' || new.sort_order);
    end if;
  else
    if old.content is distinct from new.content then
      insert into public.content_audit
        (org_id, project_id, surface, item_key, action,
         actor_type, actor_id, actor_name, before_text, after_text)
      values
        (new.org_id, new.project_id, v_surface, v_key, 'updated',
         a.actor_type, a.actor_id, a.actor_name,
         old.content, new.content);
    end if;
  end if;
  return coalesce(new, old);
end;
$$;
