-- 092_assign_work: assign a work item to a principal (US-9.9).
--
-- A work item can name an intended owner — a person or an agent. Assignment is
-- the coordination primitive; it does NOT change who may claim (the pool stays
-- open, US-3.2) — a nullable assignee_id is inert at the claim path. Setting or
-- clearing the assignee requires the manage_work capability, enforced by a
-- trigger so the specific column is gated without column-level RLS and without
-- restricting other issue writes (which stay on is_org_member). Reads are open
-- to every org member.
alter table public.issues
  add column assignee_id uuid references public.principals(id) on delete set null;

create index issues_assignee_idx on public.issues (assignee_id);

create or replace function public.guard_issue_assignee()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  if TG_OP = 'UPDATE' and new.assignee_id is not distinct from old.assignee_id then
    return new;  -- assignee unchanged (e.g. a runner updating status) — no gate
  end if;

  if new.assignee_id is not null then
    if not public.has_org_capability(new.org_id, 'manage_work') then
      raise exception 'not authorized to assign work';
    end if;
    if not exists (
      select 1 from public.organization_members
      where org_id = new.org_id and principal_id = new.assignee_id
    ) then
      raise exception 'assignee is not a member of this organization';
    end if;
  elsif TG_OP = 'UPDATE' then
    -- clearing an assignee still requires manage_work
    if not public.has_org_capability(new.org_id, 'manage_work') then
      raise exception 'not authorized to assign work';
    end if;
  end if;

  return new;
end;
$$;

create trigger issues_guard_assignee
  before insert or update on public.issues
  for each row execute function public.guard_issue_assignee();
