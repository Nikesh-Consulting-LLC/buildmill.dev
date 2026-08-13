-- 015_archive_delete: soft-remove (archive/abandon) and hard-delete for
-- projects and tasks (US-1.25). A running/queued task can't be deleted
-- or abandoned outright — the guard trigger blocks it server-side so the
-- rule holds no matter which client makes the call.

alter table public.projects add column archived_at timestamptz;
alter table public.tasks add column abandoned_at timestamptz;

create index projects_active_idx on public.projects (org_id) where archived_at is null;
create index tasks_active_idx on public.tasks (project_id) where abandoned_at is null;

create or replace function public.guard_task_removal()
returns trigger
language plpgsql
as $$
begin
  if TG_OP = 'DELETE' then
    if old.status in ('queued', 'running') then
      raise exception 'Cannot delete a task that is queued or running.';
    end if;
    return old;
  end if;

  if new.abandoned_at is not null
     and old.abandoned_at is null
     and new.status in ('queued', 'running') then
    raise exception 'Cannot abandon a task that is queued or running.';
  end if;
  return new;
end;
$$;

create trigger tasks_guard_delete
  before delete on public.tasks
  for each row execute function public.guard_task_removal();

create trigger tasks_guard_abandon
  before update of abandoned_at on public.tasks
  for each row execute function public.guard_task_removal();
