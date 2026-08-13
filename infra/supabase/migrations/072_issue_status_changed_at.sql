-- 072_issue_status_changed_at: an honest "time in state" for Things to Do
-- (US-6.4). issues.updated_at bumps on any edit — a title tweak or an
-- instruction-set append resets it — so the "waiting Xh" age lied. This adds
-- a status_changed_at that only moves when status actually changes, via a
-- trigger mirroring the existing touch_updated_at() pattern.

alter table public.issues
  add column if not exists status_changed_at timestamptz not null default now();

-- Best available proxy for rows that predate the column.
update public.issues set status_changed_at = updated_at;

create or replace function public.touch_status_changed_at()
returns trigger
language plpgsql
as $$
begin
  if new.status is distinct from old.status then
    new.status_changed_at = now();
  end if;
  return new;
end;
$$;

drop trigger if exists issues_touch_status on public.issues;
create trigger issues_touch_status
  before update on public.issues
  for each row execute function public.touch_status_changed_at();
