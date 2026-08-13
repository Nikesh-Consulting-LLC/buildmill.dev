-- 069_guideline_recommendations: graded guideline change recommendations
-- from agents (US-5.32). Agents never write guidelines — they propose;
-- the manager decides from Things to Do. Severity is agent-declared and
-- advisory: nothing auto-applies at any level.
--
-- Workers submit over MCP through the API's service connection (no
-- client insert policy). Decisions ride the RPC below so applying the
-- text and stamping the decision is one transaction under the caller's
-- RLS — the us-5.33 content_audit triggers attribute the resulting
-- guidelines change to the deciding manager automatically.

create table public.guideline_recommendations (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  project_id uuid not null references public.projects(id) on delete cascade,
  worker_id uuid references public.workers(id) on delete set null,
  section_id uuid references public.project_guidelines(id) on delete set null,
  section_key text not null default '',
  section_title text not null default '',
  severity text not null check (
    severity in ('trivial', 'minor', 'major', 'severe')
  ),
  proposed_text text not null,
  rationale text not null,
  status text not null default 'pending' check (
    status in ('pending', 'accepted', 'rejected')
  ),
  decided_by uuid,
  decided_at timestamptz,
  decision_note text,
  created_at timestamptz not null default now()
);

create index guideline_recommendations_pending_idx
  on public.guideline_recommendations (org_id, created_at)
  where status = 'pending';
create index guideline_recommendations_project_idx
  on public.guideline_recommendations (project_id, created_at desc);

alter table public.guideline_recommendations enable row level security;

create policy "members read their org guideline recommendations"
  on public.guideline_recommendations for select
  using (public.is_org_member(org_id));

-- The decide RPC updates rows under the caller's identity; members need
-- the update grant for it. No insert policy: submissions arrive only
-- through the API's service connection.
create policy "members decide their org guideline recommendations"
  on public.guideline_recommendations for update
  using (public.is_org_member(org_id))
  with check (public.is_org_member(org_id));

-- Accept applies the proposed text (creating the section when the
-- recommendation proposed a new one) and stamps the decision; reject
-- stamps only. SECURITY INVOKER: RLS decides who may decide, and the
-- guidelines write is attributed to the manager in content_audit.
create or replace function public.decide_guideline_recommendation(
  p_recommendation uuid,
  p_accept boolean,
  p_note text default ''
)
returns json
language plpgsql
security invoker
as $$
declare
  rec record;
  v_section uuid;
begin
  select * into rec
    from public.guideline_recommendations
   where id = p_recommendation and status = 'pending'
   for update;
  if not found then
    raise exception 'recommendation not found or already decided';
  end if;
  if p_accept then
    if rec.section_id is not null then
      update public.project_guidelines
         set content = rec.proposed_text
       where id = rec.section_id;
      v_section := rec.section_id;
    else
      insert into public.project_guidelines
        (org_id, project_id, section_key, title, content, sort_order)
      values
        (rec.org_id, rec.project_id, 'custom',
         coalesce(nullif(rec.section_title, ''), 'Agent-recommended section'),
         rec.proposed_text,
         coalesce((
           select max(sort_order) + 1
             from public.project_guidelines
            where project_id = rec.project_id
         ), 0))
      returning id into v_section;
    end if;
  end if;
  update public.guideline_recommendations
     set status = case when p_accept then 'accepted' else 'rejected' end,
         decided_by = auth.uid(),
         decided_at = now(),
         decision_note = nullif(p_note, '')
   where id = p_recommendation;
  return json_build_object(
    'status', case when p_accept then 'accepted' else 'rejected' end,
    'section_id', v_section
  );
end;
$$;
