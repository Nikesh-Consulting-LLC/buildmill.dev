-- 082_release_versions: system-computed release versions V<epic>.<seq>
-- (US-7.14). The factory mints the version at a release cut — never hand-picked.
-- major = the active epic's number; seq = a per-(project, epic) monotonic
-- counter starting at 1. Not semver: the number encodes which chapter (epic)
-- and how many releases in.

create table public.release_versions (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  project_id uuid not null,
  epic_id uuid not null,
  major int not null,
  seq int not null,
  version text generated always as ('V' || major || '.' || seq) stored,
  git_tag text,
  commit_sha text,
  cut_by uuid,
  included_items jsonb not null default '[]'::jsonb,
  created_at timestamptz not null default now(),
  unique (id, org_id),
  unique (project_id, epic_id, seq),
  foreign key (project_id, org_id)
    references public.projects (id, org_id) on delete cascade,
  foreign key (epic_id, org_id)
    references public.epics (id, org_id) on delete cascade
);

create index release_versions_project_idx
  on public.release_versions (project_id, created_at desc);
create index release_versions_org_idx on public.release_versions (org_id);

alter table public.release_versions enable row level security;

create policy "members read their org release versions"
  on public.release_versions for select
  using (public.is_org_member(org_id));

-- Writes go through the cut RPC (which is security invoker → the member's RLS
-- still governs the underlying reads); a direct insert policy is not needed
-- and deliberately omitted so versions are only minted via cut_release_version.

-- The atomic mint: computes major from the active epic and the next per-epic
-- seq under an advisory lock. Idempotent — re-cutting the same head returns the
-- existing version rather than minting a duplicate.
create or replace function public.cut_release_version(
  p_project uuid,
  p_commit_sha text,
  p_included jsonb default '[]'::jsonb
)
returns public.release_versions
language plpgsql
as $$
declare
  v_org uuid;
  v_epic public.epics%rowtype;
  v_existing public.release_versions%rowtype;
  v_seq int;
  v_new public.release_versions%rowtype;
begin
  select org_id into v_org from public.projects where id = p_project;
  if v_org is null then
    raise exception 'project not found';
  end if;
  select * into v_epic from public.epics where project_id = p_project and active;
  if not found then
    raise exception 'project has no active epic';
  end if;

  -- Idempotency: the same head is not minted twice (no new content).
  if p_commit_sha is not null then
    select * into v_existing from public.release_versions
      where project_id = p_project and commit_sha = p_commit_sha
      order by created_at desc limit 1;
    if found then
      return v_existing;
    end if;
  end if;

  perform pg_advisory_xact_lock(
    hashtext('release-version:' || p_project::text || ':' || v_epic.id::text)
  );
  select coalesce(max(seq), 0) + 1 into v_seq
    from public.release_versions
    where project_id = p_project and epic_id = v_epic.id;

  insert into public.release_versions
    (org_id, project_id, epic_id, major, seq, commit_sha, cut_by, included_items)
  values
    (v_org, p_project, v_epic.id, v_epic.number, v_seq, p_commit_sha,
     auth.uid(), coalesce(p_included, '[]'::jsonb))
  returning * into v_new;
  return v_new;
end;
$$;

revoke execute on function public.cut_release_version(uuid, text, jsonb)
  from public, anon;
grant execute on function public.cut_release_version(uuid, text, jsonb)
  to authenticated, service_role;

-- Set the git tag once the API has created it on GitHub.
create or replace function public.set_release_version_tag(
  p_version_id uuid, p_git_tag text
)
returns void
language sql
as $$
  update public.release_versions set git_tag = p_git_tag where id = p_version_id;
$$;

revoke execute on function public.set_release_version_tag(uuid, text)
  from public, anon;
grant execute on function public.set_release_version_tag(uuid, text)
  to authenticated, service_role;
