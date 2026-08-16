-- 271_the_pass_is_decided_by_a_manager (us-100.5, found on live 2026-08-15):
-- Apply all / Reject pass answered "refresh not found".
--
-- 268 wrote decide_guidelines_refresh as SECURITY INVOKER, on the pattern of
-- the per-row decide. But `guideline_refreshes` has only a SELECT policy —
-- the bundle was always closed by settle_guideline_refresh (173), a
-- SECURITY DEFINER trigger, never by the manager's own role. So the
-- function's `select … for update` on the refresh saw no row under RLS
-- (FOR UPDATE applies UPDATE policies), raised "refresh not found", and the
-- pass could not be decided.
--
-- The fix is the shape the rest of this feature already uses: DEFINER, with
-- the authorization stated in the body — the caller must hold
-- manage_project on the refresh's org, which is what accepting requires
-- anyway (it writes projects.agent_instructions, whose UPDATE policy is that
-- capability). The per-row writes then run as definer too, so a manager who
-- may decide the pass can decide every row of it.

create or replace function public.decide_guidelines_refresh(
  p_refresh uuid,
  p_accept boolean,
  p_note text default ''
)
returns json
language plpgsql
security definer
set search_path = public
as $$
declare
  v_ref record;
  v_rec record;
  v_applied int := 0;
  v_rejected int := 0;
begin
  select * into v_ref
    from public.guideline_refreshes
   where id = p_refresh
   for update;
  if not found then
    raise exception 'refresh not found';
  end if;
  if not public.has_org_capability(v_ref.org_id, 'manage_project') then
    raise exception 'not authorized: deciding an instructions refresh needs manage_project';
  end if;
  if v_ref.status <> 'pending' then
    raise exception 'refresh already decided';
  end if;

  for v_rec in
    select id from public.guideline_recommendations
     where refresh_id = p_refresh and status = 'pending'
     order by created_at
  loop
    perform public.decide_guideline_recommendation(v_rec.id, p_accept, p_note);
    if p_accept then v_applied := v_applied + 1; else v_rejected := v_rejected + 1; end if;
  end loop;

  update public.guideline_refreshes
     set status = 'decided', decided_at = now()
   where id = p_refresh and status = 'pending';

  return json_build_object(
    'accepted', p_accept,
    'applied', v_applied,
    'rejected', v_rejected
  );
end;
$$;

revoke all on function public.decide_guidelines_refresh(uuid, boolean, text) from public, anon;
grant execute on function public.decide_guidelines_refresh(uuid, boolean, text) to authenticated;
