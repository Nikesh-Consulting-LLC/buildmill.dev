-- 271_a_release_case_knows_its_section (us-101.2): a release's checklist gets
-- a running order.
--
-- A release's cases are rendered ordered by `issue_id`, then `title` — which
-- is to say, ordered by a UUID. UAT is worked top to bottom and the order is
-- part of the instruction ("the happy path first, because every refusal below
-- assumes the happy path's object exists"), so the order has to be a property
-- of the case rather than an accident of its key.
--
-- Three columns, all nullable-or-defaulted, all additive:
--
--   section   which part of the run this belongs to. FREE TEXT on purpose —
--             the factory names five it expects and orders them, and a
--             release that genuinely needs "Data migration" gets it rather
--             than a refused hand-back. A refusal costs a whole agent run.
--   sort      position within the section.
--   critical  the checks a test suite cannot make for you. A BADGE ONLY:
--             release_signoff_blocker (241) is deliberately untouched, so
--             every active case still needs a verdict and none may fail.
--             Whether critical should block harder is a product decision
--             nobody has made, and 241 gates the whole release lifecycle.
--
-- Then attach_release_inherited_cases v3, replacing 242's body forward. The
-- copy carries the three new columns so an inherited case lands in the
-- running order instead of as an unsorted tail; where the source case has no
-- section (every case that exists today), it defaults to 'regression' —
-- inherited cases are, by construction, the checks the release did not
-- author. 242's `is not distinct from` dedup and its project_id filter are
-- preserved exactly: always-on cases carry a null issue_id and both are
-- load-bearing.

alter table public.test_cases
  add column if not exists section text,
  add column if not exists sort integer,
  add column if not exists critical boolean not null default false;

comment on column public.test_cases.section is
  'us-101.2: which part of a release''s run this check belongs to '
  '(pre-flight / happy-path / refusals / regression / other). Free text: the '
  'factory orders the ones it knows and appends the ones it does not. Null '
  'on a library case that has never been part of a release.';

comment on column public.test_cases.sort is
  'us-101.2: position within the section. Null sorts last, then by title.';

comment on column public.test_cases.critical is
  'us-101.2: a check the test suite cannot make for you — "open the diff and '
  'confirm both branches'' changes are present". Display only; '
  'release_signoff_blocker treats every active case identically.';

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
     execution, suite_id, spec_ref, always_on_uat,
     section, sort, critical)
  select tc.org_id, tc.project_id, p_release, tc.issue_id, tc.title, tc.steps,
         tc.expected_result, tc.source, tc.test_types, tc.environments, 'active',
         tc.execution, tc.suite_id, tc.spec_ref, tc.always_on_uat,
         -- us-101.2: an inherited case is a check the release did not author.
         coalesce(tc.section, 'regression'), tc.sort, coalesce(tc.critical, false)
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
