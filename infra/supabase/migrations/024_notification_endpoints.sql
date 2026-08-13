-- 024_notification_endpoints: deployment notifications (US-1.44).
--
-- notification_endpoints is the org-level registry — deliberately generic
-- so later event families (task/review events) reuse the same endpoints.
-- Webhook URLs are secrets (Slack URLs embed tokens): they live in the
-- private data bucket at <org_id>/notifications/<endpoint_id>/url, written
-- and read by `api` only; the row keeps just a display host. Rows are
-- written via `api` (service role) — clients get SELECT.
--
-- deployment_notifications holds per-deployment event selection (no
-- secrets, plain CRUD under RLS from the SDK). No row = the default set
-- (failures + rollbacks); nothing at all is sent until an endpoint exists.

create table public.notification_endpoints (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  name text not null,
  url_host text not null default '',
  format text not null default 'json' check (format in ('json', 'slack')),
  last_delivery_at timestamptz,
  last_delivery_ok boolean,
  last_delivery_error text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (org_id, name)
);

create index notification_endpoints_org_idx on public.notification_endpoints (org_id);

alter table public.notification_endpoints enable row level security;

create policy "members read their org notification endpoints"
  on public.notification_endpoints for select
  using (public.is_org_member(org_id));

create trigger notification_endpoints_touch
  before update on public.notification_endpoints
  for each row execute function public.touch_updated_at();

create table public.deployment_notifications (
  deployment_id uuid primary key,
  org_id uuid not null references public.organizations(id) on delete cascade,
  events jsonb not null default '["failed", "rolled_back"]'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (deployment_id, org_id)
    references public.deployments (id, org_id) on delete cascade
);

create index deployment_notifications_org_idx
  on public.deployment_notifications (org_id);

alter table public.deployment_notifications enable row level security;

create policy "members manage their org deployment notifications"
  on public.deployment_notifications for all
  using (public.is_org_member(org_id))
  with check (public.is_org_member(org_id));

create trigger deployment_notifications_touch
  before update on public.deployment_notifications
  for each row execute function public.touch_updated_at();
