-- 248_feature_code_preview: the feature-level build confirm could never show
-- its instructions.
--
-- preview_issue_instructions routed every kind through dispatch_kind_for,
-- which models ONE issue's dispatch and demands an approved plan artifact on
-- that issue. A feature never holds a plan — its stories do — so the
-- BatchPhaseDialog's "Build N stories as one run?" confirm has answered
-- "code run requires an approved plan" since us-49.1 whenever it previewed
-- the feature's code instructions (first driven live 2026-08-13, FEAT-2.8).
--
-- A feature's code batch is dispatch_feature_batch's domain: by the time the
-- dialog renders, feature_dispatch_phase has already established the build is
-- ready (every sibling plan approved). The preview takes the caller's word
-- for the kind on a feature and previews exactly what the seeder would write
-- for the feature-owned code run (migrations 139/169 seed the FEATURE).

create or replace function public.preview_issue_instructions(p_issue uuid, p_kind text default null)
returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare
  v_org uuid;
  v_type text;
  v_existing text;
  v_kind text;
begin
  select org_id, type, instruction_set into v_org, v_type, v_existing
  from public.issues where id = p_issue;
  if not found then
    raise exception 'issue not found';
  end if;
  if not public.is_org_member(v_org) then
    raise exception 'not a member of this org';
  end if;

  if p_kind = 'code' and v_type = 'feature' then
    -- The feature-owned build (us-22.10/139): buildability was established by
    -- feature_dispatch_phase before this preview is ever asked for, and
    -- dispatch_kind_for would refuse over the plan artifact the feature
    -- rightly does not hold.
    v_kind := 'code';
  elsif p_kind is null or p_kind in ('plan', 'code') then
    v_kind := public.dispatch_kind_for(p_issue, p_kind);
  else
    v_kind := p_kind;
  end if;

  -- A non-empty set is what the run will read, verbatim: the seeder skips an
  -- item that already carries one, so nothing is "about to be written" here.
  if v_existing is not null and length(trim(v_existing)) > 0 then
    return jsonb_build_object(
      'kind', v_kind,
      'seeded', false,
      'instruction_set', v_existing
    );
  end if;

  return jsonb_build_object(
    'kind', v_kind,
    'seeded', true,
    'instruction_set', public.build_issue_instructions(p_issue, v_kind)
  );
end;
$$;

revoke all on function public.preview_issue_instructions(uuid, text) from public;
grant execute on function public.preview_issue_instructions(uuid, text) to authenticated, service_role;
