-- 225_skip_content_audit_during_org_delete: record_content_audit() (067)
-- logs every project/guidelines/learnings/worker-instructions delete by
-- INSERTing a new content_audit row carrying that row's org_id. During a
-- cascaded org delete, a project's own AFTER DELETE fires while (or after)
-- the organizations row is already gone, so that insert fails its own FK
-- check ("insert or update on content_audit violates foreign key
-- constraint") — logging a deletion that only matters because the org is
-- about to stop existing, into a table now missing that org.
--
-- Same escape-hatch shape as force_delete_issues (046) and the immutable
-- trigger's cascade exception (224): a transaction-local flag, set only by
-- admin_force_delete_org, so an ordinary single-item delete still logs
-- exactly as before.
create or replace function public.record_content_audit()
returns trigger
language plpgsql
security definer
set search_path to 'public'
as $function$
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
    -- Guidelines-only columns (title, sort_order) live in their own
    -- branch: plpgsql resolves every record field an expression names,
    -- guarded or not, so other surfaces must never mention them.
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
    -- Reorders steer workers as much as text edits (US-5.33 AC).
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
    -- Content changes only — skip pure updated_at touches.
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
$function$;
