-- 016_platform_admin: seeds the platform-admin org and the
-- is_platform_admin() check every admin capability gates on (US-1.27).
-- Deploy precondition: kaushlesh@nikesh.llc must already have an
-- auth.users account when this migration runs, or the seed membership
-- is skipped (with a warning) and must be added manually afterward.

alter table public.organizations add column is_platform_admin boolean not null default false;

create or replace function public.is_platform_admin()
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (
    select 1
    from public.organization_members m
    join public.organizations o on o.id = m.org_id
    where m.user_id = (select auth.uid())
      and o.is_platform_admin = true
  );
$$;

revoke execute on function public.is_platform_admin() from public, anon;
grant execute on function public.is_platform_admin() to authenticated;

do $$
declare
  v_org_id uuid;
  v_user_id uuid;
begin
  select id into v_org_id
  from public.organizations
  where is_platform_admin = true
  limit 1;

  if v_org_id is null then
    insert into public.organizations (name, is_platform_admin)
    values ('Nikesh Consulting LLC', true)
    returning id into v_org_id;
  end if;

  select id into v_user_id from auth.users where lower(email) = lower('kaushlesh@nikesh.llc') limit 1;

  if v_user_id is null then
    raise warning 'kaushlesh@nikesh.llc has no auth.users account yet — platform admin org created with no members. Add them to organization_members manually (role=owner) once the account exists.';
  else
    insert into public.organization_members (org_id, user_id, role)
    values (v_org_id, v_user_id, 'owner')
    on conflict (org_id, user_id) do nothing;
  end if;
end $$;
