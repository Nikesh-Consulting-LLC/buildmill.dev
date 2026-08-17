-- 279_an_agents_projects_are_its_grants (us-110.1): retire workers.project_id.
--
-- Migration 216 introduced a second, narrower project scope alongside the
-- worker_capabilities grant list, on the theory that an MCP token should serve
-- one project. In practice it asked the manager the same question twice, in
-- two vocabularies, and the Add agent wizard's two helper sentences ended up
-- contradicting each other: "only ever sees one project's pool" under the
-- single-select, "does whatever the roles allow on every project checked"
-- under the multi-select. The code sided with the first, so an agent created
-- with two projects checked silently never claimed the second's runs.
--
-- The grant list already does this job. list_worker_pool applies the US-31.3
-- fail-closed capability filter before any scope narrowing, so an unscoped
-- worker is offered exactly the runs whose project it is allow-listed for --
-- and claim_work re-checks the same predicate through worker_run_refusal. The
-- machine/pool provisioner has never set project_id at all; every agent Build
-- Mill provisions for itself is already unscoped and gated purely on grants.
--
-- Dropping the column WIDENS: a worker whose scope was narrower than its
-- grants can now claim the rest of what it was granted. Nothing narrows, no
-- token is invalidated, and no worker loses access to anything. On prod at
-- the time of writing, 28 of 63 workers were scoped and only three had a
-- scope narrower than their grants (one of them active).
--
-- What replaces the column's second job -- being the default project_id for
-- the no-claim MCP tools -- is application-side (us-110.1): the worker's sole
-- granted project when it has exactly one, and an explicit argument otherwise,
-- with the pool listings now returning a project id to pass.
--
-- APPLY ORDER -- this one is not "both projects, same change". A drop is the
-- one migration shape where code must ship first: the deployed Team page
-- selects workers.project_id and calls set_worker_project, so dropping ahead
-- of the release 400s that page for live users. Applied to build-mill-dev
-- with this branch; applied to Software-Factory as part of the release that
-- carries the code below it.

drop index if exists public.workers_project_idx;

alter table public.workers
  drop column if exists project_id;

-- Assigning a scope is no longer a thing that can be done.
drop function if exists public.set_worker_project(uuid, uuid);

-- create_worker returns to its four-argument form. Body is 217's verbatim,
-- minus the p_project validation and the project_id insert column.
drop function if exists public.create_worker(uuid, text, text, uuid, uuid);
drop function if exists public.create_worker(uuid, text, text, uuid);

create or replace function public.create_worker(
  p_org uuid,
  p_name text,
  p_type text,
  p_user_id uuid default null
)
returns table(worker_id uuid, token text)
language plpgsql
security definer
set search_path = public
as $$
declare
  v_token text;
  v_principal uuid;
  v_secret_id uuid;
begin
  if not public.has_org_capability(p_org, 'develop') then
    raise exception 'not authorized';
  end if;
  if p_name is null or length(trim(p_name)) = 0 then
    raise exception 'name required';
  end if;
  if p_type not in ('autonomous', 'human') then
    raise exception 'invalid worker type';
  end if;

  if p_type = 'human' then
    if p_user_id is not null then
      select id into v_principal from public.principals where auth_user_id = p_user_id;
    else
      select id into v_principal from public.principals where auth_user_id = (select auth.uid());
    end if;
    if v_principal is null then
      raise exception 'no principal for user';
    end if;
    if not exists (
      select 1 from public.organization_members
      where org_id = p_org and principal_id = v_principal
    ) then
      raise exception 'linked user is not an org member';
    end if;
  else
    if (
      select count(*)
      from public.organization_members om
      join public.principals pr on pr.id = om.principal_id
      where om.org_id = p_org and pr.kind = 'agent'
    ) >= (select max_agents from public.organizations where id = p_org) then
      raise exception 'This org has reached its agent limit.';
    end if;

    insert into public.principals (kind, display_name)
    values ('agent', trim(p_name))
    returning id into v_principal;
    insert into public.organization_members (org_id, principal_id, role)
    values (p_org, v_principal, 'agent');
  end if;

  v_token := 'sfw_' || encode(extensions.gen_random_bytes(24), 'hex');
  v_secret_id := vault.create_secret(v_token, 'worker_token:' || gen_random_uuid()::text);

  return query
  insert into public.workers
    (org_id, name, type, user_id, principal_id, token_hash, token_last4, vault_secret_id)
  values (p_org, trim(p_name), p_type, p_user_id, v_principal,
          encode(extensions.digest(v_token, 'sha256'), 'hex'),
          right(v_token, 4), v_secret_id)
  returning id, v_token;
end;
$$;

revoke execute on function public.create_worker(uuid, text, text, uuid) from public, anon;
grant execute on function public.create_worker(uuid, text, text, uuid) to authenticated;
