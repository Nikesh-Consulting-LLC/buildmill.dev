-- 213_client_perf_events: US-62.7 -- a page says how long it took to load.
-- No client-side performance capture existed before this anywhere in the
-- app. One row per Web Vitals metric per page view (CLS/LCP/INP/TTFB/FCP,
-- via Next's own `useReportWebVitals`, the same metrics any RUM tool
-- measures, self-hosted instead of sent to a third-party service).
--
-- Insert-only from the browser, under RLS, for the same reason "build less
-- API" already puts plain CRUD through the Supabase client directly. No
-- select policy: only the API's service-role connection reads this, via the
-- superadmin performance page (us-62.9) -- matching api_request_log's
-- (migration 212) default-deny-to-clients shape.

create table public.client_perf_events (
  id bigserial primary key,
  org_id uuid references public.organizations(id) on delete cascade,
  user_id uuid,
  -- The route TEMPLATE, not the raw interpolated path (`/issues/:id`, not
  -- `/issues/<uuid>`) -- normalized client-side, since the App Router does
  -- not hand a client component its matched route pattern.
  route text not null,
  metric text not null,
  value numeric not null,
  navigation_type text,
  created_at timestamptz not null default now()
);

create index client_perf_events_route_created_idx
  on public.client_perf_events (route, created_at desc);

alter table public.client_perf_events enable row level security;

create policy "authenticated users record their own perf events"
  on public.client_perf_events for insert
  to authenticated
  with check (
    user_id = auth.uid()
    and (org_id is null or public.is_org_member(org_id))
  );
