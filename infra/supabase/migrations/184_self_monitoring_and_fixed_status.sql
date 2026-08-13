-- 184_self_monitoring_and_fixed_status (US-16.8, US-16.9): the factory turns
-- the reporting machinery on itself.
--
-- Build Mill is an app Build Mill deploys, so it reports the same way any
-- other app does — same table, same fingerprint dedup, same promotion into a
-- `bug`. What this migration adds is only: which deployment counts as "the
-- factory itself", a terminal status for a system error somebody has fixed,
-- the platform-admin read that the superadmin console needs, and the editable
-- prompt that console hands to an LLM.

-- ------------------------------------------------- the self deployment
alter table public.deployments
  add column is_self_monitoring boolean not null default false;

comment on column public.deployments.is_self_monitoring is
  'US-16.8: this deployment is Build Mill itself. Its reports are the factory''s own errors and are the only rows a platform admin can read across orgs.';

-- One per org, enforced rather than assumed: two self deployments would split
-- the factory's own errors across two inboxes, and the console would show half.
create unique index deployments_one_self_monitoring_per_org
  on public.deployments (org_id)
  where is_self_monitoring;

-- ------------------------------------------------------- the fixed status
-- `fixed` is terminal and deliberately outside app_issues_open_fingerprint_key
-- (which covers only 'new'/'triaged'), so the same crash arriving after a fix
-- opens a FRESH row rather than incrementing a closed one. A regression looks
-- like a new bug because it is one.
alter table public.app_issues drop constraint app_issues_status_check;
alter table public.app_issues add constraint app_issues_status_check
  check (status in ('new', 'triaged', 'promoted', 'ignored', 'fixed'));

-- ------------------------------------------- the platform admin's window
-- Scoped to self-monitoring deployments only. The admin console is not a way
-- around the org boundary US-16.1 draws: a platform admin reads what the
-- FACTORY reported, never what somebody's customers' apps reported.
create policy "platform admins read system issues"
  on public.app_issues for select
  using (
    public.is_platform_admin()
    and exists (
      select 1 from public.deployments d
      where d.id = app_issues.deployment_id and d.is_self_monitoring
    )
  );

create policy "platform admins triage system issues"
  on public.app_issues for update
  using (
    public.is_platform_admin()
    and exists (
      select 1 from public.deployments d
      where d.id = app_issues.deployment_id and d.is_self_monitoring
    )
  )
  with check (
    public.is_platform_admin()
    and exists (
      select 1 from public.deployments d
      where d.id = app_issues.deployment_id and d.is_self_monitoring
    )
  );

-- --------------------------------------------------------- the fix prompt
-- US-16.9's "Copy fix prompt" is LLM-facing text, so it lives where every
-- other LLM-facing text in this app lives — overridable in
-- llm_prompt_templates without a deploy — rather than as a string inside a
-- React component.
create or replace function public.baked_system_issue_fix_prompt()
returns text
language sql
immutable
as $$
  select $prompt$You are debugging Build Mill, an AI-driven software delivery platform. Its web app is Next.js (App Router) in `apps/web`, its API is FastAPI in `apps/api`, and its database is Supabase Postgres with RLS on every table (migrations in `infra/supabase/migrations`).

The error below was reported by the running application itself.

{{REPORT}}

Please:
1. Find the root cause in the source, not the symptom. Read the code around the stack frames before proposing anything.
2. Say plainly what conditions produce it — if the trace is not enough to be sure, say what you would need rather than guessing.
3. Propose the smallest change that actually fixes the cause, and name what it might break.
4. Do not suppress the error, widen a try/except, or add a null check that hides the real problem, unless you can show the failure genuinely is a missing value in normal use.$prompt$;
$$;

create or replace function public.effective_system_issue_fix_prompt()
returns text
language sql
stable
security definer
set search_path = public
as $$
  select coalesce(
    public.prompt_template_override('system_issue/fix_prompt'),
    public.baked_system_issue_fix_prompt()
  );
$$;

revoke execute on function public.effective_system_issue_fix_prompt() from public, anon;
grant execute on function public.effective_system_issue_fix_prompt() to authenticated;
