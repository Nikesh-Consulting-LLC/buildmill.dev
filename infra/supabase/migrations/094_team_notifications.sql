-- 094_team_notifications: in-app notifications (US-9.12).
--
-- A team member is notified when something needs them — work assigned (US-9.9),
-- a run routed to them for review (US-9.10), or their work blocked (a
-- clarification raised). Recipients are HUMAN principals (agents act on claims,
-- not notifications). Emission is server-side, in triggers on the tables where
-- the event is written; the client never inserts. No self-noise: you are not
-- notified of your own action.

create table public.notifications (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  recipient_id uuid not null references public.principals(id) on delete cascade,
  type text not null,
  payload jsonb not null default '{}'::jsonb,
  read_at timestamptz,
  created_at timestamptz not null default now()
);

create index notifications_recipient_idx
  on public.notifications (recipient_id, created_at desc);

alter table public.notifications enable row level security;

-- A principal reads/updates ONLY its own notifications; no client insert/delete.
create policy "recipients read their notifications"
  on public.notifications for select
  using (
    recipient_id in (
      select id from public.principals where auth_user_id = (select auth.uid())
    )
  );

create policy "recipients update their notifications"
  on public.notifications for update
  using (
    recipient_id in (
      select id from public.principals where auth_user_id = (select auth.uid())
    )
  )
  with check (
    recipient_id in (
      select id from public.principals where auth_user_id = (select auth.uid())
    )
  );

-- Live bell (US-6.1 realtime pattern).
alter publication supabase_realtime add table public.notifications;

-- Emit: work assigned to a human (not yourself).
create or replace function public.notify_issue_assignment()
returns trigger language plpgsql security definer set search_path = public as $$
declare
  v_actor uuid;
begin
  if new.assignee_id is null then return new; end if;
  if TG_OP = 'UPDATE' and new.assignee_id is not distinct from old.assignee_id then
    return new;
  end if;
  if not exists (select 1 from public.principals where id = new.assignee_id and kind = 'human') then
    return new;
  end if;
  v_actor := (select id from public.principals where auth_user_id = (select auth.uid()));
  if v_actor is not null and v_actor = new.assignee_id then return new; end if;

  insert into public.notifications (org_id, recipient_id, type, payload)
  values (new.org_id, new.assignee_id, 'assigned',
          jsonb_build_object('issue_id', new.id, 'title', new.title));
  return new;
end;
$$;

create trigger issues_notify_assignment
  after insert or update on public.issues
  for each row execute function public.notify_issue_assignment();

-- Emit: a run routed to a human reviewer (not yourself).
create or replace function public.notify_run_reviewer()
returns trigger language plpgsql security definer set search_path = public as $$
declare
  v_actor uuid;
  v_title text;
begin
  if new.reviewer_id is null then return new; end if;
  if TG_OP = 'UPDATE' and new.reviewer_id is not distinct from old.reviewer_id then
    return new;
  end if;
  if not exists (select 1 from public.principals where id = new.reviewer_id and kind = 'human') then
    return new;
  end if;
  v_actor := (select id from public.principals where auth_user_id = (select auth.uid()));
  if v_actor is not null and v_actor = new.reviewer_id then return new; end if;

  select title into v_title from public.issues where id = new.issue_id;
  insert into public.notifications (org_id, recipient_id, type, payload)
  values (new.org_id, new.reviewer_id, 'review_requested',
          jsonb_build_object('run_id', new.id, 'issue_id', new.issue_id, 'title', v_title));
  return new;
end;
$$;

create trigger runs_notify_reviewer
  after insert or update on public.runs
  for each row execute function public.notify_run_reviewer();

-- Emit: work blocked — a clarification raised — notifies the item's human assignee.
create or replace function public.notify_clarification_blocked()
returns trigger language plpgsql security definer set search_path = public as $$
declare
  v_assignee uuid;
  v_title text;
  v_org uuid;
begin
  select assignee_id, title, org_id into v_assignee, v_title, v_org
  from public.issues where id = new.issue_id;
  if v_assignee is null then return new; end if;
  if not exists (select 1 from public.principals where id = v_assignee and kind = 'human') then
    return new;
  end if;

  insert into public.notifications (org_id, recipient_id, type, payload)
  values (coalesce(new.org_id, v_org), v_assignee, 'blocked',
          jsonb_build_object('issue_id', new.issue_id, 'title', v_title,
                             'question', new.question));
  return new;
end;
$$;

create trigger clarifications_notify_blocked
  after insert on public.clarifications
  for each row execute function public.notify_clarification_blocked();
