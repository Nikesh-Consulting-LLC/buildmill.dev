-- 242_inherit_automated_cases: release inheritance carries automation and
-- honors the always-on flag (US-81.5).
--
-- attach_release_inherited_cases v2, replacing the live body (verified
-- identical on both projects). Two changes:
--
-- 1. The copy carries the automation columns (execution, suite_id, spec_ref,
--    always_on_uat), so a release-scoped copy of an automated case can be
--    matched by (suite_id, spec_ref) when the pipeline reports (us-81.4).
-- 2. Active cases flagged always_on_uat attach to every release of their
--    project even when they belong to no included work item — the
--    person-flagged "this runs on every UAT" suite, for manual and automated
--    cases alike.
--
-- The dedup predicate moves to `is not distinct from` because always-on
-- cases legitimately carry a null issue_id, and the new project_id filter is
-- load-bearing: issue membership no longer implies the project scope.

create or replace function public.attach_release_inherited_cases(p_release uuid)
returns integer
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

  insert into public.test_cases
    (org_id, project_id, release_id, issue_id, title, steps, expected_result,
     source, test_types, environments, status,
     execution, suite_id, spec_ref, always_on_uat)
  select tc.org_id, tc.project_id, p_release, tc.issue_id, tc.title, tc.steps,
         tc.expected_result, tc.source, tc.test_types, tc.environments, 'active',
         tc.execution, tc.suite_id, tc.spec_ref, tc.always_on_uat
  from public.test_cases tc
  where tc.status = 'active'
    and tc.release_id is null
    and tc.project_id = v_rel.project_id
    and (
      tc.issue_id in (
        select (item->>'issue_id')::uuid
        from jsonb_array_elements(v_rel.included_items) item
        where item->>'issue_id' is not null
      )
      or tc.always_on_uat
    )
    and not exists (
      select 1 from public.test_cases dup
      where dup.release_id = p_release
        and dup.issue_id is not distinct from tc.issue_id
        and dup.title = tc.title
    );
  get diagnostics v_n = row_count;

  if v_n > 0 then
    update public.releases
    set cases_attached_at = coalesce(cases_attached_at, now()), updated_at = now()
    where id = p_release;
  end if;
  return v_n;
end;
$$;
