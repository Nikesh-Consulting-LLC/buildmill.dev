-- 067_content_audit: append-only audit trail for the four steering
-- surfaces — Overview (project record fields incl. the environment
-- block), Guidelines sections, the Learnings document, and Worker
-- Instructions (US-5.33). Capture is writer-agnostic: database triggers
-- fire for web-UI CRUD under RLS (attributed to auth.uid()), for API
-- service-role writes (attributed via app.audit_actor_* session config),
-- and for system paths like template seeding (attributed 'system').
-- Rows are produced ONLY by the triggers: no client write policy exists,
-- and a guard trigger rejects update/delete for every role.

create table public.content_audit (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  project_id uuid not null,
  surface text not null check (
    surface in ('project', 'guidelines', 'learnings', 'worker_instructions')
  ),
  item_key text not null default '',
  action text not null check (action in ('created', 'updated', 'deleted')),
  actor_type text not null check (actor_type in ('user', 'worker', 'system')),
  actor_id uuid,
  actor_name text not null default '',
  before_text text,
  after_text text,
  created_at timestamptz not null default now()
);

create index content_audit_project_idx
  on public.content_audit (project_id, created_at desc);
create index content_audit_org_idx
  on public.content_audit (org_id, created_at desc);

alter table public.content_audit enable row level security;

-- Members read their org's trail. Deliberately NO insert/update/delete
-- policies: RLS default-deny rejects every client write — never add one.
create policy "members read their org content audit"
  on public.content_audit for select
  using (public.is_org_member(org_id));

-- Append-only enforced by mechanism, not convention — even the service
-- role hits this trigger.
create or replace function public.content_audit_immutable()
returns trigger
language plpgsql
as $$
begin
  raise exception 'content_audit is append-only';
end;
$$;

create trigger content_audit_no_rewrite
  before update or delete on public.content_audit
  for each row execute function public.content_audit_immutable();

-- Who is writing? RLS user first (web UI CRUD), then the API-declared
-- actor (set_config('app.audit_actor_*') on the service connection),
-- else 'system' (seed triggers, migrations).
create or replace function public.content_audit_actor(
  out actor_type text, out actor_id uuid, out actor_name text
)
returns record
language plpgsql
security definer
set search_path = public
as $$
begin
  if auth.uid() is not null then
    actor_type := 'user';
    actor_id := auth.uid();
    select coalesce(
             nullif(u.raw_user_meta_data ->> 'full_name', ''),
             nullif(u.raw_user_meta_data ->> 'name', ''),
             u.email,
             'member'
           )
      into actor_name
      from auth.users u
     where u.id = auth.uid();
    actor_name := coalesce(actor_name, 'member');
  elsif nullif(current_setting('app.audit_actor_name', true), '') is not null then
    actor_type := coalesce(
      nullif(current_setting('app.audit_actor_type', true), ''), 'worker'
    );
    begin
      actor_id := nullif(current_setting('app.audit_actor_id', true), '')::uuid;
    exception when others then
      actor_id := null;
    end;
    actor_name := current_setting('app.audit_actor_name', true);
  else
    actor_type := 'system';
    actor_id := null;
    actor_name := 'factory';
  end if;
end;
$$;

-- The capture trigger. tg_argv[0] names the surface; each branch only
-- touches the columns its table actually has. SECURITY DEFINER so the
-- insert clears content_audit's default-deny RLS for UI writers.
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
$$;

create trigger projects_content_audit
  after insert or update on public.projects
  for each row execute function public.record_content_audit('project');

create trigger project_guidelines_content_audit
  after insert or update or delete on public.project_guidelines
  for each row execute function public.record_content_audit('guidelines');

create trigger project_learnings_content_audit
  after insert or update or delete on public.project_learnings
  for each row execute function public.record_content_audit('learnings');

create trigger worker_instructions_content_audit
  after insert or update or delete on public.worker_instructions
  for each row execute function public.record_content_audit('worker_instructions');
