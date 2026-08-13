-- 241_suite_results_gate: suite results reach the cases and the gate (US-81.4).
--
-- Two changes. release_test_results learns which suite run recorded a result,
-- so a machine-recorded pass/fail is visibly a machine's (noted_by stays null
-- for those rows — the pipeline writes with the service role).
--
-- And release_signoff_blocker v2, replacing the live body (verified identical
-- on both projects before this was written): the existing checks are
-- unchanged, plus (a) the build on UAT must still be this release's build —
-- somebody redeploying UAT mid-testing silently invalidates every result —
-- and (b) every gating suite (blocks_signoff = true, the non-default) must
-- have a succeeded or waived latest run for this release. Suites without the
-- flag never block: their results are advisory display.

alter table public.release_test_results
  add column suite_run_id uuid references public.suite_runs(id) on delete set null;

comment on column public.release_test_results.suite_run_id is
  'US-81.4: set when the suite pipeline recorded this result. A row with '
  'suite_run_id and null noted_by is a machine verdict.';

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
  v_uat_sha text;
  v_suite record;
  v_run public.suite_runs%rowtype;
begin
  select * into v_rel from public.releases where id = p_release;
  if not found then
    return 'release not found';
  end if;
  if v_rel.status <> 'uat-deployed' then
    return format('release is %s - sign-off applies to a release on UAT', v_rel.status);
  end if;

  -- US-81.4: the build under test must still be the build on UAT.
  select dr.commit_sha into v_uat_sha
  from public.deployment_runs dr
  join public.projects p on p.release_uat_deployment_id = dr.deployment_id
  where p.id = v_rel.project_id
    and dr.status = 'succeeded'
    and dr.commit_sha is not null
  order by dr.created_at desc
  limit 1;
  if v_uat_sha is not null and v_uat_sha <> v_rel.commit_sha then
    return format('UAT now runs %s, not this release''s %s - redeploy before signing off',
                  substr(v_uat_sha, 1, 8), substr(v_rel.commit_sha, 1, 8));
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

  -- US-81.4: every gating suite's latest run for this release must have
  -- succeeded, or carry a waiver. A waiver lives on the run, so a re-run
  -- produces a fresh, unwaived verdict.
  for v_suite in
    select ts.id, ts.name
    from public.test_suites ts
    where ts.project_id = v_rel.project_id
      and ts.status = 'active' and ts.run_on_uat and ts.blocks_signoff
    order by ts.name
  loop
    select * into v_run
    from public.suite_runs sr
    where sr.release_id = p_release and sr.suite_id = v_suite.id
    order by sr.created_at desc
    limit 1;
    if not found then
      return format('suite %s has not run for this release yet', v_suite.name);
    end if;
    if v_run.status in ('queued', 'running') then
      return format('suite %s is still running', v_suite.name);
    end if;
    if v_run.status <> 'succeeded' and v_run.waived_at is null then
      if v_run.status = 'error' then
        return format('suite %s could not run - re-run or waive it', v_suite.name);
      end if;
      return format('suite %s %s - re-run or waive it', v_suite.name,
                    case v_run.status when 'failed' then 'failed'
                                      when 'timed-out' then 'timed out'
                                      else v_run.status end);
    end if;
  end loop;

  return null;
end;
$$;
