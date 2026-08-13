-- 209_dispatch_kind_for_failed_is_buildable: an auto-inferred dispatch of a
-- single failed story sent it back to planning instead of coding.
--
-- Observed 2026-08-01 on US-2.3: a code run failed on its turn ceiling
-- (max_turns), landing the story on `failed`. The manager pressed Dispatch
-- (no explicit kind — `dispatch_issue(p_issue, null)`, the normal button).
-- `dispatch_kind_for`'s own `v_can_code` predicate already reads
-- `has_approved_plan and status in ('planned', 'needs-fixes', 'failed')` —
-- 'failed' has been a build-phase status there since it was written. But the
-- `p_kind is null` (inferred) branch never used that predicate; it
-- re-derived the same decision inline and simply forgot 'failed' in the
-- code-eligible set, so a failed story with an already-approved plan fell
-- through to the plan branch (`status in ('draft', 'ready', 'failed')`,
-- unconditional on whether a plan exists) and was silently re-planned.
--
-- This is the exact incident class migration 146 (US-27.11) fixed for
-- `dispatch_feature_batch` on 2026-07-26 — six approved-plan stories sent
-- back to planning by a batch dispatch. That fix never reached this sibling
-- function, the one every single-story Dispatch button actually calls.
--
-- The fix is one status added to the inferred branch's first condition, so
-- it agrees with `v_can_code` (and with the explicit `p_kind = 'code'` path,
-- which already gates on `v_can_code` and was never wrong). Nothing else
-- changes: a 'planned' story with no approved plan artifact — a data
-- anomaly that should not occur — still raises exactly as before, rather
-- than silently gaining a new inferred outcome nobody asked this fix for.
create or replace function public.dispatch_kind_for(p_issue uuid, p_kind text default null::text)
returns text
language plpgsql
stable
as $function$
declare
  v_status text;
  v_type text;
  v_has_approved_plan boolean;
  v_can_plan boolean;
  v_can_code boolean;
  v_kind text;
begin
  select status, type into v_status, v_type
  from public.issues where id = p_issue;
  if not found then
    raise exception 'issue not found';
  end if;

  select exists(
    select 1 from public.artifacts
    where issue_id = p_issue and kind = 'plan' and status = 'approved'
  ) into v_has_approved_plan;

  v_can_plan := v_status in ('draft', 'ready', 'failed', 'needs-fixes', 'planned');
  v_can_code := v_has_approved_plan
                and v_status in ('planned', 'needs-fixes', 'failed');

  if p_kind is null then
    -- US-27.11's own condition, restated: 'failed' with an approved plan is
    -- a build-phase status, same as 'planned' and 'needs-fixes' already were.
    if v_has_approved_plan and v_status in ('planned', 'needs-fixes', 'failed') then
      v_kind := 'code';
    elsif v_status in ('draft', 'ready', 'failed') then
      v_kind := 'plan';
    elsif v_status = 'needs-fixes' and not v_has_approved_plan then
      v_kind := 'plan';
    else
      raise exception 'issue is not dispatchable from status "%"', v_status;
    end if;
  elsif p_kind = 'plan' then
    if not v_can_plan then
      raise exception 'issue is not dispatchable for planning from status "%"', v_status;
    end if;
    v_kind := 'plan';
  elsif p_kind = 'code' then
    if not v_has_approved_plan then
      raise exception 'code run requires an approved plan';
    end if;
    if not v_can_code then
      raise exception 'issue is not dispatchable for coding from status "%"', v_status;
    end if;
    v_kind := 'code';
  else
    raise exception 'unknown run kind "%" — expected "plan" or "code"', p_kind;
  end if;

  if v_type = 'feature' and v_kind = 'plan' then
    raise exception 'a feature is not planned directly — approve its PRD and break it into stories, then plan those';
  end if;

  if v_kind = 'code' and not v_has_approved_plan then
    raise exception 'code run requires an approved plan';
  end if;

  return v_kind;
end;
$function$;

comment on function public.dispatch_kind_for(uuid, text) is
  'US-27.11 / US-57.13: which run kind a Dispatch click means. A failed story '
  'that already holds an approved plan infers "code", matching '
  '`dispatch_feature_batch`''s build-phase guard rather than falling through '
  'to a silent re-plan.';
