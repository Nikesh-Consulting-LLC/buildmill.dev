-- 182_app_issues_and_report_keys (US-16.1): a deployed app gets a place to
-- report into, and a credential to report with.
--
-- Two things, deliberately kept apart from the work-item pipeline:
--
-- 1. `app_issues` — an inbox, one row per distinct problem rather than per
--    occurrence. A crash repeating a thousand times increments a counter on
--    one row; the partial unique index below is what makes that safe under
--    concurrent ingestion rather than merely intended.
-- 2. Four columns on `deployments` — a per-deployment report key, hashed for
--    authentication and *revealable* from Vault. This is the worker-token
--    shape (048_worker_token_reveal.sql), not the write-only Vault shape used
--    for LLM keys, and the difference is deliberate: this key ships inside a
--    client-side bundle, so treating it as unrecoverable would only make
--    setup worse without buying real secrecy. Abuse is bounded by the
--    per-key rate limit and the deployment scope (US-16.2), not by hiding it.
--
-- Nothing here can write to `issues`. Promotion is US-16.7's RPC.

-- ------------------------------------------------------------- app_issues
create table public.app_issues (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  project_id uuid not null,
  deployment_id uuid not null,
  source text not null check (source in ('automated', 'user_report')),
  -- sha256(error_type + normalized_message + top 3 frames), computed by the
  -- ingestion endpoint. Null for user reports — a human's description is not
  -- deduplicated against anything.
  fingerprint text,
  occurrence_count integer not null default 1 check (occurrence_count > 0),
  first_seen_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now(),
  title text not null,
  message text,
  stack_trace text,
  context jsonb not null default '{}'::jsonb,
  reporter_name text,
  reporter_email text,
  status text not null default 'new'
    check (status in ('new', 'triaged', 'promoted', 'ignored')),
  promoted_issue_id uuid,
  triaged_by uuid references public.principals(id) on delete set null,
  triaged_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  -- Cross-org integrity: composite FKs, per 020_deployments' reasoning — FK
  -- validation bypasses RLS, so a plain FK would let a row in org A point at
  -- org B's project.
  foreign key (project_id, org_id)
    references public.projects (id, org_id) on delete cascade,
  foreign key (deployment_id, org_id)
    references public.deployments (id, org_id) on delete cascade,
  -- a promoted work item that is later deleted leaves the report standing,
  -- with its link cleared rather than the report disappearing with it.
  foreign key (promoted_issue_id, org_id)
    references public.issues (id, org_id) on delete set null (promoted_issue_id),
  -- a user report carries a reporter; an automated one carries a fingerprint.
  constraint app_issues_fingerprint_source
    check (source = 'user_report' or fingerprint is not null)
);

create index app_issues_org_idx on public.app_issues (org_id);
create index app_issues_project_idx on public.app_issues (project_id);
create index app_issues_deployment_idx on public.app_issues (deployment_id);
-- the hub's default view: what still needs a decision, newest first.
create index app_issues_triage_idx
  on public.app_issues (org_id, status, last_seen_at desc);

-- Dedup is a database rule, not an endpoint convention. Two occurrences of
-- the same crash arriving at once cannot both insert: one wins, the other's
-- insert conflicts and is retried as an increment. Terminal rows are excluded
-- so a crash that returns after being ignored opens a fresh row rather than
-- resurrecting a closed one.
create unique index app_issues_open_fingerprint_key
  on public.app_issues (deployment_id, fingerprint)
  where fingerprint is not null and status in ('new', 'triaged');

alter table public.app_issues enable row level security;

-- Read and triage for org members. There is deliberately NO insert policy:
-- every row is written by `api`'s service-role connection after it has
-- validated the report key itself (US-16.2). A browser session can never
-- insert a report, so nothing in the app can forge one.
create policy "members read their org app issues"
  on public.app_issues for select
  using (public.is_org_member(org_id));

create policy "members triage their org app issues"
  on public.app_issues for update
  using (public.is_org_member(org_id))
  with check (public.is_org_member(org_id));

create policy "members delete their org app issues"
  on public.app_issues for delete
  using (public.is_org_member(org_id));

create trigger app_issues_touch
  before update on public.app_issues
  for each row execute function public.touch_updated_at();

-- ------------------------------------------------- deployment report keys
alter table public.deployments
  add column issue_reporting_enabled boolean not null default false,
  add column issue_report_key_hash text,
  add column issue_report_key_last4 text,
  add column issue_report_key_vault_secret_id uuid;

comment on column public.deployments.issue_reporting_enabled is
  'US-16.1: opt-in. False makes the ingestion endpoint answer the same generic 401 as a bad key — an instant kill switch that does not rotate the key.';

-- Mint (or rotate) the deployment's report key. Rotation is immediate and
-- has no grace period: the old key stops authenticating the moment this
-- returns, which is why US-16.3 confirms before calling it.
create or replace function public.generate_deployment_report_key(p_deployment uuid)
returns text
language plpgsql
security definer
set search_path = public
as $$
declare
  v_org uuid;
  v_secret_id uuid;
  v_key text;
begin
  select org_id, issue_report_key_vault_secret_id into v_org, v_secret_id
  from public.deployments where id = p_deployment;
  if v_org is null or not public.is_org_member(v_org) then
    raise exception 'not authorized';
  end if;

  v_key := 'sfr_' || encode(extensions.gen_random_bytes(24), 'hex');

  if v_secret_id is null then
    v_secret_id := vault.create_secret(
      v_key, 'deployment_report_key:' || p_deployment::text);
  else
    perform vault.update_secret(v_secret_id, v_key);
  end if;

  update public.deployments
  set issue_report_key_hash = encode(extensions.digest(v_key, 'sha256'), 'hex'),
      issue_report_key_last4 = right(v_key, 4),
      issue_report_key_vault_secret_id = v_secret_id
  where id = p_deployment;

  return v_key;
end;
$$;

-- Show the key again later. Same boundary as the "members manage their org
-- deployments" policy already grants — this is a new thing that policy's
-- membership test permits, not a wider one.
create or replace function public.reveal_deployment_report_key(p_deployment uuid)
returns text
language plpgsql
security definer
set search_path = public
as $$
declare
  v_org uuid;
  v_secret_id uuid;
  v_key text;
begin
  select org_id, issue_report_key_vault_secret_id into v_org, v_secret_id
  from public.deployments where id = p_deployment;
  if v_org is null or not public.is_org_member(v_org) then
    raise exception 'not authorized';
  end if;
  if v_secret_id is null then
    raise exception 'no report key for this deployment — generate one first';
  end if;

  select decrypted_secret into v_key
  from vault.decrypted_secrets where id = v_secret_id;

  return v_key;
end;
$$;

revoke execute on function public.generate_deployment_report_key(uuid) from public, anon;
revoke execute on function public.reveal_deployment_report_key(uuid) from public, anon;
grant execute on function public.generate_deployment_report_key(uuid) to authenticated;
grant execute on function public.reveal_deployment_report_key(uuid) to authenticated;
