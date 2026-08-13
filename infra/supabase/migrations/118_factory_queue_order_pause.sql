-- US-15.2 — The factory queue: manager-set order and pause.
--
-- Two per-run controls so the manager can steer the autonomous queue:
--   queue_rank  — the manager's execution order. NULL means "unordered",
--                 which sorts after ranked runs by created_at (new work goes
--                 to the back of a hand-ordered queue). Lower rank = earlier.
--   paused_at   — a paused run stays queued and keeps its context, but is
--                 never offered to a worker until resumed.
--
-- The pool (list_worker_pool) and the atomic claim (claim_run) honour both,
-- so a worker polling MCP is offered runs in the manager's order and never a
-- paused one — enforced server-side, not just drawn in the UI.

alter table public.runs
  add column if not exists queue_rank double precision,
  add column if not exists paused_at timestamptz;

-- Reorder a project's queued runs: assign ranks in the given order. The array
-- is the manager's full ordered list of run ids for that project's queue.
create or replace function public.reorder_factory_queue(
  p_project uuid, p_run_ids uuid[]
) returns void
language plpgsql security definer set search_path = public as $$
declare
  v_org uuid;
begin
  select org_id into v_org from public.projects where id = p_project;
  if v_org is null or not public.is_org_member(v_org) then
    raise exception 'not authorized';
  end if;
  update public.runs r
    set queue_rank = t.ord
  from unnest(p_run_ids) with ordinality as t(run_id, ord)
  where r.id = t.run_id
    and r.project_id = p_project
    and r.org_id = v_org
    and r.status = 'queued';
end;
$$;

-- Pause or resume a single queued run.
create or replace function public.set_run_paused(
  p_run uuid, p_paused boolean
) returns void
language plpgsql security definer set search_path = public as $$
declare
  v_org uuid;
begin
  select org_id into v_org from public.runs where id = p_run;
  if v_org is null or not public.is_org_member(v_org) then
    raise exception 'not authorized';
  end if;
  update public.runs
    set paused_at = case when p_paused then now() else null end
    where id = p_run and status = 'queued';
end;
$$;

revoke execute on function public.reorder_factory_queue(uuid, uuid[]) from public, anon;
revoke execute on function public.set_run_paused(uuid, boolean) from public, anon;
grant execute on function public.reorder_factory_queue(uuid, uuid[]) to authenticated;
grant execute on function public.set_run_paused(uuid, boolean) to authenticated;
