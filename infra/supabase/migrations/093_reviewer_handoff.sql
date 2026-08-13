-- 093_reviewer_handoff: route a run to a named reviewer (US-9.10).
--
-- A submitted run can be routed to a principal with review_work (owner / admin
-- / lead / reviewer), human or agent, who approves it or sends it back. The
-- reviewer_id is set by a manage_work or review_work member; the reviewer must
-- themselves be a review_work-capable active member. Merge stays authoritative
-- (US-1.12) — this adds *who* it's waiting on, not a new merge mechanism.
alter table public.runs
  add column reviewer_id uuid references public.principals(id) on delete set null;

create index runs_reviewer_idx on public.runs (reviewer_id);

create or replace function public.guard_run_reviewer()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  -- Skip the gate unless the reviewer is actually being set or changed, so the
  -- factory can INSERT/UPDATE runs (no auth context) without tripping it.
  if TG_OP = 'INSERT' and new.reviewer_id is null then
    return new;
  end if;
  if TG_OP = 'UPDATE' and new.reviewer_id is not distinct from old.reviewer_id then
    return new;
  end if;

  -- Only a manage_work or review_work member may route for review.
  if not (public.has_org_capability(new.org_id, 'manage_work')
          or public.has_org_capability(new.org_id, 'review_work')) then
    raise exception 'not authorized to route for review';
  end if;

  -- The chosen reviewer must be a review_work-capable active member.
  if new.reviewer_id is not null and not exists (
    select 1
    from public.organization_members m
    join public.role_capabilities rc
      on rc.role = m.role and rc.capability = 'review_work' and rc.allowed = true
    where m.org_id = new.org_id
      and m.principal_id = new.reviewer_id
      and m.status = 'active'
  ) then
    raise exception 'reviewer must be a review-capable member of this organization';
  end if;

  return new;
end;
$$;

create trigger runs_guard_reviewer
  before insert or update on public.runs
  for each row execute function public.guard_run_reviewer();
