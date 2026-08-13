-- 143_agent_server_write_policies: let an admin actually register an agent
-- server (US-26.1 defect).
--
-- Migration 142 created SELECT policies only, on the stated assumption that
-- `api` writes these tables with the service role the way runner_config does.
-- It does not: `routers/agent_servers.py` writes them through PostgREST with
-- the CALLER'S OWN JWT (postgrest_post / postgrest_patch), which is the
-- "build less API" pattern the rest of the app uses for plain CRUD. With no
-- write policy, RLS default-deny refused every one of those writes with 42501
-- — for an owner who had already passed both `is_org_member` and the router's
-- `manage_org` check. Registering a machine was impossible.
--
-- The fix is policies, not a switch to the service role: RLS then enforces the
-- capability itself, so the router's check becomes a friendlier 403 rather
-- than the only thing standing between a viewer and the fleet. This mirrors
-- 087_role_capability_layer.sql, which gates organization_members writes on
-- `has_org_capability(org_id, 'manage_members')` the same way.
--
-- Deliberately NOT granted to clients:
--   * INSERT/DELETE on agent_slots — slots are created and retired by the job
--     engine (`agent_provision.py`, direct Postgres) alongside real work on the
--     machine. A client-side insert would mint a row with no service behind it.
--   * anything on agent_server_jobs — a job row is a record of what the server
--     did over SSH; a client that could write one could fake an install.
--   * DELETE on agent_servers — teardown soft-removes (status = 'removed') so
--     the job history survives.

-- Register a machine (US-26.1).
create policy "admins register agent servers"
  on public.agent_servers for insert
  with check (public.has_org_capability(org_id, 'manage_org'));

-- Edit its definition: workdir, modules, extras, template (US-26.3 / US-26.6).
-- The USING clause decides which rows are visible to update; WITH CHECK stops
-- an admin moving a row into another org.
create policy "admins edit agent servers"
  on public.agent_servers for update
  using (public.has_org_capability(org_id, 'manage_org'))
  with check (public.has_org_capability(org_id, 'manage_org'));

-- Enable / pause a slot (US-26.5) — the desired_state write that
-- PATCH /agent-servers/{id}/slots/{slot_id} makes on the caller's behalf.
create policy "admins set slot desired state"
  on public.agent_slots for update
  using (public.has_org_capability(org_id, 'manage_org'))
  with check (public.has_org_capability(org_id, 'manage_org'));
