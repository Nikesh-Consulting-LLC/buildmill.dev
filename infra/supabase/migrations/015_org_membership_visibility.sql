-- 015_org_membership_visibility: make organization_members.user_id embeddable
-- with public.profiles for PostgREST nested selects, and let org members
-- see their teammates' profile rows.
--
-- The Members settings UI (apps/web/src/app/(app)/settings/members-settings.tsx)
-- queries organization_members with a nested `profiles(email, display_name)`
-- select. PostgREST can only auto-embed across a direct foreign key between
-- the two tables in the query. organization_members.user_id currently only
-- references auth.users(id); profiles.id independently references
-- auth.users(id) 1:1. Adding a second FK from organization_members.user_id
-- to public.profiles(id) is safe (same key space, profiles.id is a PK that
-- mirrors auth.users(id)) and is the standard pattern for enabling PostgREST
-- embeds without a schema redesign.
--
-- Even with the embed resolvable, profiles RLS only allowed a user to see
-- their own row (`id = auth.uid()`), so every teammate's embed would come
-- back null. This adds an additional (OR'd) select policy letting an org
-- member see the profiles of everyone who shares at least one org with them.

alter table public.organization_members
  add constraint organization_members_user_id_profiles_fkey
  foreign key (user_id) references public.profiles(id) on delete cascade;

create policy "org members can view teammates' profiles"
  on public.profiles for select
  using (
    exists (
      select 1
      from public.organization_members mine
      join public.organization_members theirs on theirs.org_id = mine.org_id
      where mine.user_id = (select auth.uid())
        and theirs.user_id = profiles.id
    )
  );
