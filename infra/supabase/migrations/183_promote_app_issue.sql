-- 183_promote_app_issue (US-16.7): the hinge the phase turns on.
--
-- Everything before this is inbox and triage. This is where a report crosses
-- into the pipeline APPLICATION.md already documents — and after it, a report
-- is just a link: the work item is a normal `bug`, subject to every existing
-- rule, with no special-casing anywhere downstream.
--
-- One transaction, like dispatch_issue and approve_run, rather than three
-- client-side writes that can half-fail and leave a report pointing at a work
-- item that does not exist.

create or replace function public.promote_app_issue(
  p_app_issue uuid,
  p_epic_id uuid default null
)
returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
  v_report public.app_issues%rowtype;
  v_issue uuid;
  v_principal uuid;
  v_body text;
begin
  select * into v_report from public.app_issues where id = p_app_issue;
  if v_report.id is null then
    raise exception 'report not found';
  end if;
  -- Platform admins act on the factory's own errors (US-16.9); everyone else
  -- must be a member of the reporting org.
  if not (public.is_org_member(v_report.org_id) or public.is_platform_admin()) then
    raise exception 'not authorized';
  end if;

  -- Guarded rather than idempotent on purpose: a second promotion would mint
  -- a second work item for one bug, and silently succeeding at that is worse
  -- than refusing.
  if v_report.status in ('promoted', 'ignored') then
    raise exception 'this report is already % and cannot be promoted', v_report.status;
  end if;

  if p_epic_id is not null and not exists (
    select 1 from public.epics
    where id = p_epic_id and org_id = v_report.org_id
      and project_id = v_report.project_id
  ) then
    raise exception 'epic does not belong to this report''s project';
  end if;

  -- The description carries what the manager already has in front of them, so
  -- promotion is one action rather than a re-typing exercise.
  v_body := coalesce(v_report.message, '');
  if v_report.stack_trace is not null then
    v_body := v_body || E'\n\n```\n' || v_report.stack_trace || E'\n```';
  end if;
  v_body := v_body || E'\n\n---\n' ||
    format(
      'Promoted from an app report (%s, %s occurrence(s), first seen %s, last seen %s).',
      v_report.source,
      v_report.occurrence_count,
      to_char(v_report.first_seen_at, 'YYYY-MM-DD HH24:MI'),
      to_char(v_report.last_seen_at, 'YYYY-MM-DD HH24:MI')
    );
  if v_report.reporter_email is not null or v_report.reporter_name is not null then
    v_body := v_body || format(
      E'\nReported by: %s',
      trim(both ' <>' from concat_ws(' ', v_report.reporter_name,
        case when v_report.reporter_email is not null
          then '<' || v_report.reporter_email || '>' end))
    );
  end if;

  insert into public.issues (org_id, project_id, type, epic_id, title, body)
  values (v_report.org_id, v_report.project_id, 'bug', p_epic_id,
          v_report.title, v_body)
  returning id into v_issue;

  select id into v_principal
  from public.principals where auth_user_id = (select auth.uid());

  update public.app_issues
  set status = 'promoted',
      promoted_issue_id = v_issue,
      triaged_by = v_principal,
      triaged_at = now()
  where id = p_app_issue;

  return v_issue;
end;
$$;

revoke execute on function public.promote_app_issue(uuid, uuid) from public, anon;
grant execute on function public.promote_app_issue(uuid, uuid) to authenticated;
