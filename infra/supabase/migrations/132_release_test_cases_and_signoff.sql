-- 132_release_test_cases_and_signoff: US-21.4.
--
-- A release deployed to UAT is not a release anyone has tested. The factory
-- already produces test cases — _materialize_test_plan turns an approved test
-- plan into test_cases rows on the work item — but nothing gathered them at
-- the point a human is actually about to test a build.
--
-- The release's set is assembled, not invented: every active case of every
-- included work item is COPIED onto the release, plus the regression cases the
-- agent authored in the release run (us-21.3). Copied, not referenced, because
-- a work item's cases can be edited or abandoned afterwards and a release must
-- keep the set that was actually run against it.

create table if not exists public.release_test_results (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  release_id uuid not null references public.releases(id) on delete cascade,
  test_case_id uuid not null references public.test_cases(id) on delete cascade,
  -- The vocabulary test_runs already uses, so a result reads the same
  -- wherever it was recorded.
  result text not null check (result in ('pass', 'fail', 'blocked')),
  comment text,
  noted_by uuid default auth.uid(),
  noted_at timestamptz not null default now(),
  unique (release_id, test_case_id)
);

comment on table public.release_test_results is
  'US-21.4: one manager-entered result per case per release. Blocked is not '
  'passed — sign-off means the build was tested, not that testing was tried.';

create index if not exists release_test_results_release_idx
  on public.release_test_results (release_id);

alter table public.release_test_results enable row level security;

drop policy if exists "members read their org release results"
  on public.release_test_results;
create policy "members read their org release results"
  on public.release_test_results for select
  using (public.is_org_member(org_id));

drop policy if exists "members record release results"
  on public.release_test_results;
create policy "members record release results"
  on public.release_test_results for insert
  with check (public.is_org_member(org_id));

drop policy if exists "members change release results"
  on public.release_test_results;
create policy "members change release results"
  on public.release_test_results for update
  using (public.is_org_member(org_id))
  with check (public.is_org_member(org_id));

-- Re-testing after a correction is a legitimate manager action, so a result
-- may be cleared as well as changed.
drop policy if exists "members clear release results"
  on public.release_test_results;
create policy "members clear release results"
  on public.release_test_results for delete
  using (public.is_org_member(org_id));

-- ---------------------------------------------------------------------------
-- Inherit the included work items' cases
-- ---------------------------------------------------------------------------

create or replace function public.attach_release_inherited_cases(p_release uuid)
returns int
language plpgsql
as $$
declare
  v_rel public.releases%rowtype;
  v_n int := 0;
begin
  select * into v_rel from public.releases where id = p_release;
  if not found then
    raise exception 'release not found';
  end if;

  -- Copy, never reference: `source_case_id` keeps the trail back to the work
  -- item's case, but editing that case later cannot change what this release
  -- was tested with.
  insert into public.test_cases
    (org_id, project_id, release_id, issue_id, title, steps, expected_result,
     source, test_types, environments, status)
  select tc.org_id, tc.project_id, p_release, tc.issue_id, tc.title, tc.steps,
         tc.expected_result, tc.source, tc.test_types, tc.environments, 'active'
  from public.test_cases tc
  where tc.status = 'active'
    and tc.release_id is null
    and tc.issue_id in (
      select (item->>'issue_id')::uuid
      from jsonb_array_elements(v_rel.included_items) item
      where item->>'issue_id' is not null
    )
    -- Idempotent: re-running after a re-dispatch must not duplicate a case.
    and not exists (
      select 1 from public.test_cases dup
      where dup.release_id = p_release
        and dup.issue_id = tc.issue_id
        and dup.title = tc.title
    );
  get diagnostics v_n = row_count;

  if v_n > 0 then
    update public.releases
    set cases_attached_at = coalesce(cases_attached_at, now()),
        updated_at = now()
    where id = p_release;
  end if;
  return v_n;
end;
$$;

-- ---------------------------------------------------------------------------
-- Can this release be signed off?
-- ---------------------------------------------------------------------------
-- Null = signable. Both halves must hold: the deployment actually succeeded
-- (us-21.3 records it) and every case has a result, none of them failed or
-- blocked. Either alone is insufficient — cases approved against a UAT that is
-- quietly down are not a pass, and a healthy deploy nobody tested is not
-- tested.

create or replace function public.release_signoff_blocker(p_release uuid)
returns text
language plpgsql
stable
as $$
declare
  v_rel public.releases%rowtype;
  v_total int;
  v_missing int;
  v_bad int;
begin
  select * into v_rel from public.releases where id = p_release;
  if not found then
    return 'release not found';
  end if;
  if v_rel.status <> 'uat-deployed' then
    return format('release is %s — sign-off applies to a release on UAT',
                  v_rel.status);
  end if;

  select count(*) into v_total
  from public.test_cases where release_id = p_release and status = 'active';
  if v_total = 0 then
    return 'this release has no test cases attached yet';
  end if;

  select count(*) into v_missing
  from public.test_cases tc
  where tc.release_id = p_release and tc.status = 'active'
    and not exists (
      select 1 from public.release_test_results r
      where r.release_id = p_release and r.test_case_id = tc.id
    );
  if v_missing > 0 then
    return format('%s test case%s still %s no result',
      v_missing, case when v_missing = 1 then '' else 's' end,
      case when v_missing = 1 then 'has' else 'have' end);
  end if;

  select count(*) into v_bad
  from public.release_test_results r
  join public.test_cases tc on tc.id = r.test_case_id
  where r.release_id = p_release and tc.status = 'active'
    and r.result in ('fail', 'blocked');
  if v_bad > 0 then
    return format('%s test case%s failed or blocked', v_bad,
                  case when v_bad = 1 then '' else 's' end);
  end if;

  return null;
end;
$$;

grant execute on function public.attach_release_inherited_cases(uuid) to authenticated;
grant execute on function public.release_signoff_blocker(uuid) to authenticated;

alter publication supabase_realtime add table public.release_test_results;
