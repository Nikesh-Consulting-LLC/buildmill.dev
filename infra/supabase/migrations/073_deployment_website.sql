-- 073_deployment_website: a deployment's public reachable address (US-7.2).
--
-- Today the only URL-shaped column is deployments.health_check_url, an
-- internal probe fetched over SSH. This adds the *public* Website a tester
-- (person or agent) opens to reach a running environment — a domain or an
-- IP literal. Both nullable, so existing deployments are unaffected; RLS is
-- unchanged (rides the existing org-scoped deployments policy).

alter table public.deployments
  add column website_kind text check (website_kind in ('domain', 'ip')),
  add column website_url text;

comment on column public.deployments.website_url is
  'US-7.2: the public reachable address of this deployment''s environment '
  '(distinct from the internal health_check_url). Absolute http(s) URL.';

-- Server-side guard: deployments are pure client CRUD (no API endpoint), so
-- the DB check is the server side of the validation. A website must be an
-- absolute http(s) URL whose host matches its kind — an IPv4 literal for
-- 'ip' (optional port/path), a dotted domain for 'domain' (optional port/path).
alter table public.deployments
  add constraint deployments_website_shape check (
    website_url is null or (
      website_kind is not null
      and case website_kind
        when 'ip' then
          website_url ~* '^https?://(\d{1,3}\.){3}\d{1,3}(:\d+)?(/.*)?$'
        when 'domain' then
          website_url ~* '^https?://([a-z0-9]([a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}(:\d+)?(/.*)?$'
        else false
      end
    )
  );
