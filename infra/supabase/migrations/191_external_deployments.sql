-- 191_external_deployments: a deployment does not have to be the factory's
-- job (US-50.1).
--
-- Where a team already ships through GitHub Actions or another CI system, a
-- deployment means one thing — a merge into the branch that system watches —
-- and nothing on any server. Today that cannot be written down: server_id and
-- target_folder are both not null, so a deployment without a machine is not
-- representable at all.
--
-- The server is what makes a deployment factory-run, so it is the column that
-- becomes optional. `kind` is chosen at creation and never edited: a history
-- half SSH transfer and half merge is a history that no longer means one
-- thing (redeploy and rollback both read past runs and would find payloads
-- that cannot exist on the other kind).
--
-- Deployments are pure client CRUD under RLS — no API endpoint validates them
-- — so this check constraint IS the server side of the rule, the same
-- reasoning migration 073 used for the website shape. RLS is unchanged: no
-- new table, it rides the existing org-scoped deployments policy.

alter table public.deployments
  add column kind text not null default 'factory'
    check (kind in ('factory', 'external')),
  add column target_branch text not null default '';

comment on column public.deployments.kind is
  'US-50.1: factory = this app transfers files over SSH and runs the script; '
  'external = somebody else''s pipeline ships it and a deployment is a merge '
  'into target_branch. Set at creation, never edited.';

comment on column public.deployments.target_branch is
  'US-50.1: for an external deployment, the branch the other system watches — '
  'where `branch` (the source) gets merged. Empty for a factory deployment.';

-- A machine is only mandatory for the kind that uses one.
alter table public.deployments alter column server_id drop not null;
alter table public.deployments alter column target_folder drop not null;

-- The composite FK (server_id, org_id) -> servers (id, org_id) is MATCH
-- SIMPLE, so a null server_id simply skips the check — cross-org integrity is
-- unchanged for every row that names one.
alter table public.deployments
  add constraint deployments_kind_shape check (
    case kind
      when 'factory' then server_id is not null and target_folder is not null
      when 'external' then server_id is null and btrim(target_branch) <> ''
      else false
    end
  );
