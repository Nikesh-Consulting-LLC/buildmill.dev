-- 170: US-41.2 — a feature's draft stories are curated in one action.
--
-- The feature rail refuses every bulk action until each story is out of
-- `draft` (us-15.3's look-before-you-spend gate), and then offered nothing to
-- do about it: "15 still in draft; curate them before planning" is a sentence
-- naming an action the page did not provide. Curating meant opening fifteen
-- edit dialogs — the exact friction us-41.1 removed one stage later.
--
-- The gate itself stays. Letting the bulk dispatch route straight from `draft`
-- was considered and rejected: plan runs on the live project cost $5–$15 each,
-- so fifteen stories is ~$150 against a $100 budget, and the budget refuses
-- only to START work — it cannot stop a batch already created. One click would
-- commit all of it with nobody having read the breakdown.
--
-- Transactional rather than fifteen client updates, which could half-succeed
-- and leave the rail in a state the manager did not ask for. `security
-- invoker` (the default) so RLS is the authorization, exactly as everywhere
-- else — a non-member cannot see the feature, so cannot curate its stories.

create or replace function public.curate_feature_stories(p_feature uuid)
returns int
language plpgsql
as $$
declare
  v_feature public.issues%rowtype;
  v_moved int;
begin
  select * into v_feature from public.issues where id = p_feature for update;
  if not found then
    raise exception 'feature not found';
  end if;
  if v_feature.type <> 'feature' then
    raise exception 'curation applies to a feature, not a %', v_feature.type;
  end if;

  -- Only `draft` moves. A story already past it is left exactly where it is,
  -- so a second press is a no-op and a half-curated feature finishes rather
  -- than resets.
  with moved as (
    update public.issues
    set status = 'ready'
    where parent_id = p_feature
      and abandoned_at is null
      and status = 'draft'
    returning id, org_id
  ),
  logged as (
    insert into public.issue_events (org_id, issue_id, type, payload)
    select m.org_id, m.id, 'curated',
           jsonb_build_object('feature_id', p_feature, 'from_status', 'draft')
    from moved m
    returning 1
  )
  select count(*)::int into v_moved from logged;

  return coalesce(v_moved, 0);
end;
$$;

comment on function public.curate_feature_stories(uuid) is
  'US-41.2: move every draft story under this feature to ready, in one '
  'transaction, and record a `curated` event for each. The manager saying "I '
  'have read these" — never automatic, and never applied to a story that has '
  'already moved past draft.';

revoke execute on function public.curate_feature_stories(uuid) from public;
revoke execute on function public.curate_feature_stories(uuid) from anon;
grant execute on function public.curate_feature_stories(uuid) to authenticated, service_role;

notify pgrst, 'reload schema';
