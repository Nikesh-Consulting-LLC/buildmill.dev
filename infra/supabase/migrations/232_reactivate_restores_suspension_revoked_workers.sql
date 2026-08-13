-- 232_reactivate_restores_suspension_revoked_workers: the us-55.2 defect.
--
-- Migration 089's cascade_membership_to_tokens revokes a principal's workers
-- when its membership is suspended or removed — right for a departing human,
-- and the only single action that cuts API + MCP + git access at once. But
-- the cascade is one-way: Reactivate restores the membership row and nothing
-- else. A suspend→reactivate round-trip leaves the member reading "active"
-- while every worker credential it owns is silently dead — the machine's
-- service keeps running (its control socket authenticated before the revoke),
-- keeps heartbeating last_seen_at, and can claim nothing. The dashboard then
-- reports "Nobody can take this / no agent is online" for an agent that is
-- visibly running: it killed Architect.001 twice (2026-07-29/30) and the
-- whole Sandy fleet on 2026-08-09.
--
-- The fix records WHAT suspension revoked so reactivation can restore exactly
-- that and nothing more:
--
--   * `workers.revoked_by_suspension_at` — stamped only by the suspend
--     cascade. A worker revoked any other way (the Revoke button, slot
--     removal, member removal) never carries it, so it stays revoked through
--     any suspend cycle.
--   * The cascade gains a restore branch: membership suspended→active flips
--     back only the workers it stamped, clearing the stamp. The machine's env
--     file still holds the token (nothing rewrote it), so the worker
--     reconnects on its own.
--   * A hygiene trigger on workers keeps the marker honest at the source
--     instead of trusting every revoke call site, present and future: any
--     transition to 'active' clears it (an active worker carries no
--     restoration debt), and any transition to 'revoked' that did not stamp
--     the marker in the same statement clears it too (an unstamped revoke is
--     deliberate). It fires only when status actually changes, so heartbeat
--     updates never touch it.
--
-- Member removal stays a plain revoke: there is no membership left to
-- reactivate, so nothing is stamped and nothing is restorable. Re-issue also
-- keeps its meaning — it mints a new credential regardless of markers.
--
-- The repair block at the end heals existing data, scoped to workers bound to
-- live agent slots (credentials Build Mill itself manages — a deliberately
-- revoked slot credential would have been re-issued or the slot removed):
-- an active membership with a revoked slot worker is a round-trip that
-- already happened (restore now); a suspended membership with a revoked slot
-- worker is a suspension in progress (stamp it, so Reactivate restores it).

alter table public.workers
  add column if not exists revoked_by_suspension_at timestamptz;

comment on column public.workers.revoked_by_suspension_at is
  'US-55.2: set only by the membership-suspend cascade when it revokes this '
  'worker; reactivation restores exactly the workers carrying it, then clears '
  'it. Null on a deliberately revoked worker — those stay revoked.';

-- ---------------------------------------------------------------------------
-- Marker hygiene at the source: no revoke call site can leave a stale marker.
-- ---------------------------------------------------------------------------
create or replace function public.workers_suspension_marker_hygiene()
returns trigger language plpgsql as $$
begin
  if new.status = 'active' then
    new.revoked_by_suspension_at := null;
  elsif new.status = 'revoked'
        and old.status <> 'revoked'
        and new.revoked_by_suspension_at is not distinct from old.revoked_by_suspension_at then
    -- A revoke that did not stamp the marker in the same statement is a
    -- deliberate revoke; it must survive any later reactivate.
    new.revoked_by_suspension_at := null;
  end if;
  return new;
end;
$$;

comment on function public.workers_suspension_marker_hygiene() is
  'US-55.2: only the suspend cascade''s own stamped update may leave '
  'revoked_by_suspension_at set on a revoked worker; every other status '
  'transition clears it.';

drop trigger if exists workers_suspension_marker_hygiene on public.workers;
create trigger workers_suspension_marker_hygiene
  before update on public.workers
  for each row
  when (old.status is distinct from new.status)
  execute function public.workers_suspension_marker_hygiene();

-- ---------------------------------------------------------------------------
-- The cascade: suspend stamps what it revokes; reactivate restores exactly
-- that; removal revokes without a stamp (nothing left to reactivate).
-- ---------------------------------------------------------------------------
create or replace function public.cascade_membership_to_tokens()
returns trigger language plpgsql security definer set search_path = public as $$
begin
  if TG_OP = 'DELETE'
     or (TG_OP = 'UPDATE' and new.status = 'suspended' and old.status <> 'suspended') then
    update public.workers
    set status = 'revoked',
        revoked_by_suspension_at = case when TG_OP = 'UPDATE' then now() end
    where principal_id = old.principal_id
      and org_id = old.org_id
      and status = 'active';
  elsif TG_OP = 'UPDATE' and old.status = 'suspended' and new.status = 'active' then
    update public.workers
    set status = 'active',
        revoked_by_suspension_at = null
    where principal_id = old.principal_id
      and org_id = old.org_id
      and status = 'revoked'
      and revoked_by_suspension_at is not null;
  end if;
  if TG_OP = 'DELETE' then
    return old;
  end if;
  return new;
end;
$$;

comment on function public.cascade_membership_to_tokens() is
  'US-9.6 + US-55.2: suspending or removing a membership revokes the '
  'principal''s workers in that org. Suspension stamps '
  'revoked_by_suspension_at; reactivation restores exactly the stamped '
  'workers and clears the stamp. Deliberate revokes are never stamped and '
  'stay revoked.';

-- ---------------------------------------------------------------------------
-- Repair existing trap victims (slot-bound workers only — Build Mill manages
-- those credentials, so a revoked worker on a live slot is the trap, not an
-- operator decision).
-- ---------------------------------------------------------------------------

-- Round-trip already completed: membership active, worker still revoked.
update public.workers w
set status = 'active',
    revoked_by_suspension_at = null
where w.status = 'revoked'
  and exists (
    select 1 from public.agent_slots s
    where s.worker_id = w.id and s.status = 'active'
  )
  and exists (
    select 1 from public.organization_members m
    where m.principal_id = w.principal_id
      and m.org_id = w.org_id
      and m.status = 'active'
  );

-- Suspension in progress: stamp, so the next Reactivate restores.
update public.workers w
set revoked_by_suspension_at = now()
where w.status = 'revoked'
  and w.revoked_by_suspension_at is null
  and exists (
    select 1 from public.agent_slots s
    where s.worker_id = w.id and s.status = 'active'
  )
  and exists (
    select 1 from public.organization_members m
    where m.principal_id = w.principal_id
      and m.org_id = w.org_id
      and m.status = 'suspended'
  );
