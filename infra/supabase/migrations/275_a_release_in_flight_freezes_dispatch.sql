-- 275_a_release_in_flight_freezes_dispatch (US-103.5): while a project's
-- release is in flight, its work items can still be WRITTEN but not ROUTED.
--
-- A release is pinned to one commit, and everything downstream -- the notes,
-- the UAT deploy, the promotion -- reads that SHA. Work merged while it is in
-- flight is not in the build being tested, but it IS on the default branch,
-- so it silently belongs to the next release while the manager watches it
-- merge during this one. The tested build and the branch drift apart in the
-- one window where the manager is meant to be reading UAT results rather than
-- reasoning about which commits are in what.
--
-- The rule goes where the rules already are. Migration 235 built exactly this
-- mechanism and stated the discipline: a surface may ask "can this be
-- dispatched right now, and if not, why?" and gets the answer FROM THE SAME
-- CODE THE FACTORY ENFORCES, never a re-derivation. So this is one branch in
-- issue_dispatch_refusal, and it buys three things at once: dispatch_issue
-- raises it, org_issue_dispatch_blocks reports it to the Workbench and the
-- issue page, and the button and the RPC cannot disagree.
--
-- Hard refusal, not a soft parked hold. A parked run holds a claim slot and
-- reads to the manager as work in progress; the point is that nothing is in
-- progress on this project until the release lands. And the freeze ends three
-- different ways -- released, stopped, rejected -- one of which means "this
-- build was bad". Draining a queue automatically into a rejected build's
-- aftermath is not a decision to make silently.
--
-- Frozen: `plan` and `code`, the kinds that put a worker on the repository.
-- Not frozen: `breakdown`, `elaborate`, `draw`, `guidelines`, `merge` --
-- authoring and grooming stay open, which is the manager's own line: writing
-- stories, bugs and chores is not routing them.
--
-- Body carried forward verbatim from migration 262 with the one branch added.

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
  v_release record;
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

  -- us-103.5: the release freeze. Placed before the feature-routing rules so
  -- the manager reads the reason that actually blocks them first: being told
  -- to dispatch the parent feature instead is no help when the project is
  -- frozen either way.
  --
  -- The in-flight set is NOT restated here -- it is exactly migration 215's
  -- releases_one_in_flight_per_project index, and a second definition that
  -- drifts from it is how "in flight" comes to mean two different things.
  if p_kind in ('plan', 'code') then
    select r.version, r.status, r.id into v_release
    from public.releases r
    where r.project_id = v_issue.project_id
      and r.status in ('queued', 'running', 'notes-ready', 'deploying',
                       'uat-deployed', 'uat-deploy-failed', 'uat-signed-off',
                       'promoting')
    limit 1;

    if found then
      return format(
        'Release %s is in flight (%s). New work can be written but not '
        'dispatched on this project until it is released, stopped or '
        'rejected.',
        v_release.version,
        case v_release.status
          when 'queued' then 'cut — waiting for an agent to prepare it'
          when 'running' then 'being prepared — notes, UAT deploy, health checks'
          when 'notes-ready' then 'notes written — the UAT deploy is about to fire'
          when 'deploying' then 'deploying to UAT'
          when 'uat-deployed' then 'on UAT — waiting on test results and sign-off'
          when 'uat-deploy-failed' then 'its UAT deploy failed — retry or stop it'
          when 'uat-signed-off' then 'signed off — ready to promote to production'
          when 'promoting' then 'promoting to production'
          else v_release.status
        end);
    end if;
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

comment on function public.issue_dispatch_refusal(uuid, text) is
  'The dispatch refusals as prose, so a surface can ask before offering the '
  'button and read exactly what dispatch_issue would have raised (US-74.5). '
  'us-103.5 added the release freeze: while a project has a release in '
  'flight, plan and code dispatch are refused and the reason names the '
  'release, where it is in its lifecycle, and the three ways out.';

-- Grants are unchanged by `create or replace`, but restated so a fresh
-- database built from these files in order ends up identical to prod.
revoke all on function public.issue_dispatch_refusal(uuid, text) from public, anon;
grant execute on function public.issue_dispatch_refusal(uuid, text)
  to authenticated, service_role;
