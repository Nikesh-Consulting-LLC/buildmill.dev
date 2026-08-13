-- 001_initial: org-scoped foundation — organizations, members, profiles.
-- RLS on from the first migration; a signup trigger provisions the
-- single-operator case (profile + default org + owner membership).

create table public.organizations (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  created_at timestamptz not null default now()
);

create table public.organization_members (
  org_id uuid not null references public.organizations(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  role text not null default 'owner' check (role in ('owner', 'member')),
  created_at timestamptz not null default now(),
  primary key (org_id, user_id)
);

create table public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  email text not null,
  display_name text,
  created_at timestamptz not null default now()
);

alter table public.organizations enable row level security;
alter table public.organization_members enable row level security;
alter table public.profiles enable row level security;

-- security definer so org policies can check membership without
-- recursing into organization_members' own RLS.
create or replace function public.is_org_member(org uuid)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (
    select 1
    from public.organization_members m
    where m.org_id = org
      and m.user_id = (select auth.uid())
  );
$$;

create policy "members can view their orgs"
  on public.organizations for select
  using (public.is_org_member(id));

create policy "owners can update their orgs"
  on public.organizations for update
  using (
    exists (
      select 1
      from public.organization_members m
      where m.org_id = id
        and m.user_id = (select auth.uid())
        and m.role = 'owner'
    )
  );

create policy "users can view their own memberships"
  on public.organization_members for select
  using (user_id = (select auth.uid()));

create policy "users can view their own profile"
  on public.profiles for select
  using (id = (select auth.uid()));

create policy "users can update their own profile"
  on public.profiles for update
  using (id = (select auth.uid()));

-- Provision profile + default org + owner membership on signup.
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  new_org uuid;
begin
  insert into public.profiles (id, email, display_name)
  values (new.id, new.email, split_part(new.email, '@', 1));

  insert into public.organizations (name)
  values (initcap(split_part(new.email, '@', 1)) || '''s Workspace')
  returning id into new_org;

  insert into public.organization_members (org_id, user_id, role)
  values (new_org, new.id, 'owner');

  return new;
end;
$$;

create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();
