-- 056_attachments_bucket: image attachments in markdown (US-5.16).
--
-- A private `attachments` bucket for images pasted/dropped into markdown
-- editors. Org data, NOT secrets — so unlike the policy-free `data`
-- bucket (019, unchanged here), it carries org-scoped storage.objects
-- policies, the 037 project-docs pattern. Markdown stores a stable
-- `attachment://<path>` ref, never a URL; MarkdownView signs a short-lived
-- URL at render time under the viewer's own RLS, so org isolation is
-- enforced at read time. Insert + select only: uploads are immutable and
-- orphan cleanup is an accepted v1 cost (see the story's out of scope).

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
  'attachments', 'attachments', false, 5242880,
  array['image/png', 'image/jpeg', 'image/gif', 'image/webp']
)
on conflict (id) do nothing;

create policy "org members read their attachments"
  on storage.objects for select
  using (
    bucket_id = 'attachments'
    and public.is_org_member_text((storage.foldername(name))[1])
    and (storage.foldername(name))[2] = 'uploads'
  );

create policy "org members upload attachments"
  on storage.objects for insert
  with check (
    bucket_id = 'attachments'
    and public.is_org_member_text((storage.foldername(name))[1])
    and (storage.foldername(name))[2] = 'uploads'
  );
