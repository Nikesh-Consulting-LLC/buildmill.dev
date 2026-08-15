-- 262_a_merge_names_its_branches (us-98.2): a chore carries the branches a
-- merge run will land.
--
-- Every other run kind derives its subject from the work item — the story,
-- the plan, the release. A merge derives nothing: which branches to land is
-- a decision the manager makes, and nothing in the database could hold it.
--
-- The list is the contract. us-98.3 licenses the agent to read exactly these
-- refs and no others; us-98.5 fails the run if any one of them is left
-- unmerged; us-98.6 shows the manager the same list beside the diff. So it
-- is validated where it is WRITTEN, by a trigger, rather than discovered to
-- be wrong forty minutes into a run.
--
-- Head shas are NOT stored here. They are resolved from GitHub at dispatch
-- and frozen into the run's input_context, because a sha stored on the issue
-- would silently go stale between the manager setting the list and the run
-- starting. Resolving needs a GitHub call, which SQL cannot make — hence
-- `dispatch_merge(p_issue, p_branch_heads)`, following the same
-- one-dispatcher-per-kind shape as dispatch_breakdown / dispatch_wireframe /
-- dispatch_elaboration. The api resolves, then calls this.

-- 1 ------------------------------------------------------------ the column
alter table public.issues
  add column if not exists merge_branches text[] not null default '{}';

comment on column public.issues.merge_branches is
  'us-98.2: the branches a merge run on this chore will land onto the '
  'project default branch, in the order the manager listed them. Non-empty '
  'only on a chore. A chore carrying branches dispatches as kind ''merge'' '
  'rather than ''code''. Head shas live on the run, not here — they are '
  'resolved at dispatch so they cannot go stale.';

-- 2 ---------------------------------------------------- validate on write
create or replace function public.validate_merge_branches()
returns trigger
language plpgsql
as $function$
declare
  v_default text;
  v_branch text;
begin
  if coalesce(array_length(new.merge_branches, 1), 0) = 0 then
    return new;
  end if;

  if new.type <> 'chore' then
    raise exception
      'only a chore carries merge branches — this is a % (us-98.2)', new.type;
  end if;

  foreach v_branch in array new.merge_branches loop
    if v_branch is null or btrim(v_branch) = '' then
      raise exception 'a merge branch name cannot be blank';
    end if;
    if v_branch <> btrim(v_branch) then
      raise exception
        'merge branch "%" has surrounding whitespace', v_branch;
    end if;
  end loop;

  if (select count(*) from unnest(new.merge_branches) as b)
     <> (select count(distinct b) from unnest(new.merge_branches) as b) then
    raise exception
      'the merge branch list repeats a branch — each lands exactly once';
  end if;

  select default_branch into v_default
  from public.projects where id = new.project_id;

  if v_default is not null and v_default = any(new.merge_branches) then
    raise exception
      'the default branch "%" cannot be merged into itself', v_default;
  end if;

  return new;
end;
$function$;

drop trigger if exists validate_merge_branches on public.issues;
create trigger validate_merge_branches
  before insert or update of merge_branches, type, project_id
  on public.issues
  for each row execute function public.validate_merge_branches();

-- 3 ----------------------------------------------- the kind a merge chore is
-- NOTE the `default null`: dispatch_kind_for is called both as
-- dispatch_kind_for(id) and dispatch_kind_for(id, 'code'). Omitting it here
-- fails with 42P13 "cannot remove parameter defaults from existing function",
-- which is Postgres refusing to silently break every one-argument caller.
create or replace function public.dispatch_kind_for(
  p_issue uuid,
  p_kind text default null
)
returns text
language plpgsql
stable
as $function$
declare
  v_status text;
  v_type text;
  v_has_approved_plan boolean;
  v_can_plan boolean;
  v_can_code boolean;
  v_is_merge boolean;
  v_kind text;
begin
  select status, type, coalesce(array_length(merge_branches, 1), 0) > 0
    into v_status, v_type, v_is_merge
  from public.issues where id = p_issue;
  if not found then
    raise exception 'issue not found';
  end if;

  select exists(
    select 1 from public.artifacts
    where issue_id = p_issue and kind = 'plan' and status = 'approved'
  ) into v_has_approved_plan;

  -- us-96.1: a chore has no planning phase — its one dispatchable kind is
  -- 'code', from any recoverable status, with no plan requirement.
  v_can_plan := v_type <> 'chore'
                and v_status in ('draft', 'ready', 'failed', 'needs-fixes', 'planned');
  v_can_code := case
    when v_type = 'chore'
      then v_status in ('draft', 'ready', 'failed', 'needs-fixes')
    else v_has_approved_plan
         and v_status in ('planned', 'needs-fixes', 'failed')
  end;

  if p_kind is null then
    if v_type = 'chore' then
      if not v_can_code then
        raise exception 'issue is not dispatchable from status "%"', v_status;
      end if;
      -- us-98.2: a chore carrying branches is a merge, not a build.
      v_kind := case when v_is_merge then 'merge' else 'code' end;
    elsif v_has_approved_plan and v_status in ('planned', 'needs-fixes', 'failed') then
      v_kind := 'code';
    elsif v_status in ('draft', 'ready', 'failed') then
      v_kind := 'plan';
    elsif v_status = 'needs-fixes' and not v_has_approved_plan then
      v_kind := 'plan';
    else
      raise exception 'issue is not dispatchable from status "%"', v_status;
    end if;
  elsif p_kind = 'plan' then
    if v_type = 'chore' then
      raise exception 'a chore has no planning phase — dispatch builds it';
    end if;
    if not v_can_plan then
      raise exception 'issue is not dispatchable for planning from status "%"', v_status;
    end if;
    v_kind := 'plan';
  elsif p_kind = 'code' then
    if v_is_merge then
      raise exception
        'this chore carries % branch(es) to merge — dispatch merges them, '
        'it does not build. Clear the branch list to build it instead.',
        array_length((select merge_branches from public.issues where id = p_issue), 1);
    end if;
    if v_type <> 'chore' and not v_has_approved_plan then
      raise exception 'code run requires an approved plan';
    end if;
    if not v_can_code then
      raise exception 'issue is not dispatchable for coding from status "%"', v_status;
    end if;
    v_kind := 'code';
  elsif p_kind = 'merge' then
    if v_type <> 'chore' then
      raise exception
        'only a chore is dispatched as a merge — this is a %', v_type;
    end if;
    if not v_is_merge then
      raise exception
        'this chore names no branches to merge — add at least one first';
    end if;
    if not v_can_code then
      raise exception 'issue is not dispatchable for merging from status "%"', v_status;
    end if;
    v_kind := 'merge';
  else
    raise exception
      'unknown run kind "%" — expected "plan", "code" or "merge"', p_kind;
  end if;

  if v_type = 'feature' and v_kind = 'plan' then
    raise exception 'a feature is not planned directly — approve its PRD and break it into stories, then plan those';
  end if;

  if v_kind = 'code' and v_type <> 'chore' and not v_has_approved_plan then
    raise exception 'code run requires an approved plan';
  end if;

  return v_kind;
end;
$function$;

-- 4 --------------------------------------------------------- the dispatcher
-- SECURITY INVOKER, deliberately — like dispatch_issue, dispatch_breakdown
-- and dispatch_wireframe, none of which are definer. RLS on `issues` IS the
-- authorization: the function runs as the caller, so it can only reach rows
-- their org already lets them reach. A `security definer` version of this
-- would read and mutate any issue in any org by id, which is exactly the
-- cross-org hole every table's RLS exists to prevent.
create or replace function public.dispatch_merge(
  p_issue uuid,
  p_branch_heads jsonb
)
returns uuid
language plpgsql
as $function$
declare
  v_issue public.issues%rowtype;
  v_project public.projects%rowtype;
  v_context jsonb;
  v_run uuid;
  v_pre_status text;
  v_named text[];
  v_base_head text;
begin
  select * into v_issue from public.issues where id = p_issue for update;
  if not found then
    raise exception 'issue not found';
  end if;
  if v_issue.abandoned_at is not null then
    raise exception 'issue is abandoned';
  end if;

  -- Reuses every structural refusal rather than restating them.
  if public.dispatch_kind_for(p_issue, 'merge') <> 'merge' then
    raise exception 'not dispatchable as a merge';
  end if;

  select * into v_project from public.projects where id = v_issue.project_id;

  -- The api resolved these from GitHub. They must name exactly the branches
  -- the manager listed — no more, no fewer — or the run would be licensed
  -- (us-98.3) for refs nobody approved.
  select array_agg(value ->> 'branch' order by value ->> 'branch')
    into v_named
  from jsonb_array_elements(coalesce(p_branch_heads, '[]'::jsonb));

  if coalesce(v_named, '{}') is distinct from (
       select array_agg(b order by b) from unnest(v_issue.merge_branches) as b
     ) then
    raise exception
      'the resolved branch heads do not match this chore''s branch list';
  end if;

  if exists (
    select 1 from jsonb_array_elements(p_branch_heads) e
    where coalesce(e ->> 'head_sha', '') = ''
  ) then
    raise exception 'every branch must carry a resolved head sha';
  end if;

  v_base_head := p_branch_heads -> 0 ->> 'base_head';

  v_context := jsonb_build_object(
    'title', v_issue.title,
    'type', v_issue.type,
    'story', v_issue.body,
    'body', v_issue.body,
    'acceptance_criteria', v_issue.acceptance_criteria,
    'repo_full_name', v_project.repo_full_name,
    'default_branch', v_project.default_branch,
    'run_kind', 'merge',
    'guidelines', public.assemble_project_guidelines(v_issue.project_id),
    'learnings', public.assemble_project_learnings(v_issue.project_id),
    'merge_base', jsonb_build_object(
      'branch', v_project.default_branch,
      'head_sha', v_base_head
    ),
    'merge_branches', p_branch_heads,
    'documents', coalesce((
      select jsonb_agg(jsonb_build_object(
        'id', d.id, 'name', d.name, 'mime_type', d.mime_type,
        'size_bytes', d.size_bytes, 'attached_to', d.attached_to
      ) order by d.created_at)
      from public.documents d
      where d.issue_id = p_issue and d.attached_to = 'work-item'
    ), '[]'::jsonb),
    'test_cases', '[]'::jsonb
  );

  perform public.seed_issue_instructions(p_issue, 'merge');

  v_pre_status := v_issue.status;

  insert into public.runs
    (org_id, issue_id, provider, status, kind, input_context, prev_issue_status)
  values
    (v_issue.org_id, p_issue, 'claude', 'queued', 'merge', v_context, v_pre_status)
  returning id into v_run;

  update public.issues set status = 'queued' where id = p_issue;

  insert into public.issue_events (org_id, issue_id, type, payload)
  values (
    v_issue.org_id, p_issue, 'dispatched',
    jsonb_build_object(
      'run_id', v_run,
      'kind', 'merge',
      'from_status', v_pre_status,
      'branches', to_jsonb(v_issue.merge_branches),
      'kind_chosen_by', 'manager'
    )
  );

  return v_run;
end;
$function$;

comment on function public.dispatch_merge(uuid, jsonb) is
  'us-98.2: dispatch a merge run for a chore that names branches. '
  'p_branch_heads is [{branch, head_sha, base_head}] resolved from GitHub by '
  'the api — SQL cannot make that call, and a sha stored on the issue would '
  'go stale. The set must equal the chore''s merge_branches exactly. '
  'SECURITY INVOKER like every sibling dispatcher: RLS on issues is the '
  'authorization.';

revoke all on function public.dispatch_merge(uuid, jsonb) from public, anon;
grant execute on function public.dispatch_merge(uuid, jsonb) to authenticated, service_role;

-- 5 ----------------------------------------------------- prose refusals
create or replace function public.issue_dispatch_refusal(p_issue uuid, p_kind text)
returns text
language plpgsql
stable
as $function$
declare
  v_issue public.issues%rowtype;
  v_project public.projects%rowtype;
  v_parent_label text;
  v_sibling_count int;
begin
  select * into v_issue from public.issues where id = p_issue;
  if not found then
    return null;
  end if;
  select * into v_project from public.projects where id = v_issue.project_id;

  -- us-98.2: a merge names its subject or it is not a merge.
  if p_kind = 'merge' then
    if v_issue.type <> 'chore' then
      return format('only a chore is dispatched as a merge — this is a %s',
                    v_issue.type);
    end if;
    if coalesce(array_length(v_issue.merge_branches, 1), 0) = 0 then
      return 'this chore names no branches to merge — add at least one first';
    end if;
    return null;
  end if;

  if p_kind = 'code'
     and coalesce(array_length(v_issue.merge_branches, 1), 0) > 0
  then
    return format(
      'this chore carries %s branch(es) to merge — dispatch merges them, it '
      'does not build',
      array_length(v_issue.merge_branches, 1));
  end if;

  if p_kind = 'code'
     and coalesce(v_project.route_feature_as_one, true)
     and v_issue.parent_id is not null
     and v_issue.status not in ('failed', 'needs-fixes')
  then
    select coalesce(
             case when e.number is not null and p.item_no is not null
               then 'FEAT-' || e.number || '.' || p.item_no
             end,
             p.title)
      into v_parent_label
    from public.issues p
    left join public.epics e on e.id = p.epic_id
    where p.id = v_issue.parent_id;

    select count(*) into v_sibling_count
    from public.issues c
    where c.parent_id = v_issue.parent_id and c.abandoned_at is null;

    return format('%s owns the build — dispatch the feature to build all %s stories',
      coalesce(v_parent_label, 'the feature'), v_sibling_count);
  end if;

  -- us-96.4: the feature owns the initial PLAN too.
  if p_kind = 'plan'
     and coalesce(v_project.route_feature_as_one, true)
     and v_issue.parent_id is not null
     and v_issue.status in ('draft', 'ready')
     and coalesce(current_setting('factory.feature_batch', true), '') <> '1'
     and not exists (
       select 1 from public.artifacts a
       where a.issue_id = p_issue and a.kind = 'plan'
     )
  then
    select coalesce(
             case when e.number is not null and p.item_no is not null
               then 'FEAT-' || e.number || '.' || p.item_no
             end,
             p.title)
      into v_parent_label
    from public.issues p
    left join public.epics e on e.id = p.epic_id
    where p.id = v_issue.parent_id;

    select count(*) into v_sibling_count
    from public.issues c
    where c.parent_id = v_issue.parent_id and c.abandoned_at is null;

    return format('%s owns the plan — dispatch the feature to plan all %s stories',
      coalesce(v_parent_label, 'the feature'), v_sibling_count);
  end if;

  return null;
end;
$function$;
