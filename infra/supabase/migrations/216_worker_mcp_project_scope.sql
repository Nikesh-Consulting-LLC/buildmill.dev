-- 216_worker_mcp_project_scope: single MCP endpoint (superseding the
-- /mcp/<org-shortname>[/<project-slug>] URL scheme from US-3.14).
--
-- MCP access is now scoped by workers.project_id — at most one project
-- per token — instead of a URL path segment. This is deliberately
-- separate from public.worker_capabilities, which keeps its existing
-- many-to-many meaning for git-remote access (US-3.12/3.13); a worker
-- can still hold git capability grants on several projects even though
-- its MCP endpoint now serves only one.
--
-- Backfill only covers the unambiguous case: a worker whose
-- worker_capabilities rows name exactly one project inherits that
-- project directly, so single-project workers see no behavior change.
-- A worker with zero or multiple project grants is left unscoped
-- (project_id null) rather than guessed — an unscoped worker's MCP
-- calls simply see no project until one is assigned via the app, and no
-- token is silently invalidated or reissued by this migration.

alter table public.workers
  add column project_id uuid references public.projects(id) on delete set null;

create index workers_project_idx on public.workers (project_id);

with single_project as (
  select worker_id, (array_agg(project_id))[1] as project_id
  from public.worker_capabilities
  group by worker_id
  having count(distinct project_id) = 1
)
update public.workers w
set project_id = sp.project_id
from single_project sp
where w.id = sp.worker_id;

-- Token minting: p_project is optional so existing 4-arg callers keep
-- working unscoped; the frontend is updated separately to pass it.
drop function if exists public.create_worker(uuid, text, text, uuid);

create or replace function public.create_worker(
  p_org uuid,
  p_name text,
  p_type text,
  p_user_id uuid default null,
  p_project uuid default null
)
returns table (worker_id uuid, token text)
language plpgsql
security definer
set search_path = public
as $$
declare
  v_token text;
begin
  if not public.is_org_member(p_org) then
    raise exception 'not authorized';
  end if;
  if p_name is null or length(trim(p_name)) = 0 then
    raise exception 'name required';
  end if;
  if p_type not in ('autonomous', 'human') then
    raise exception 'invalid worker type';
  end if;
  if p_user_id is not null and not exists (
    select 1 from public.organization_members m
    where m.org_id = p_org and m.user_id = p_user_id
  ) then
    raise exception 'linked user is not an org member';
  end if;
  if p_project is not null and not exists (
    select 1 from public.projects p where p.id = p_project and p.org_id = p_org
  ) then
    raise exception 'project does not belong to this org';
  end if;

  v_token := 'sfw_' || encode(extensions.gen_random_bytes(24), 'hex');

  return query
  insert into public.workers (org_id, name, type, user_id, project_id, token_hash, token_last4)
  values (p_org, trim(p_name), p_type, p_user_id, p_project,
          encode(extensions.digest(v_token, 'sha256'), 'hex'),
          right(v_token, 4))
  returning id, v_token;
end;
$$;

revoke execute on function public.create_worker(uuid, text, text, uuid, uuid) from public, anon;
grant execute on function public.create_worker(uuid, text, text, uuid, uuid) to authenticated;

-- Lets a manager assign/change a worker's MCP project after creation
-- (e.g. resolving one of the multi-project workers this migration left
-- unscoped) without regenerating its token.
create or replace function public.set_worker_project(p_worker uuid, p_project uuid)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  v_org uuid;
begin
  select org_id into v_org from public.workers where id = p_worker;
  if v_org is null then
    raise exception 'worker not found';
  end if;
  if not public.is_org_member(v_org) then
    raise exception 'not authorized';
  end if;
  if p_project is not null and not exists (
    select 1 from public.projects p where p.id = p_project and p.org_id = v_org
  ) then
    raise exception 'project does not belong to this org';
  end if;
  update public.workers set project_id = p_project where id = p_worker;
end;
$$;

revoke execute on function public.set_worker_project(uuid, uuid) from public, anon;
grant execute on function public.set_worker_project(uuid, uuid) to authenticated;
