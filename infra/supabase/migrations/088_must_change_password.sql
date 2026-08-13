-- 088_must_change_password: forced first-login password change (US-9.4/US-9.5).
--
-- An admin-provisioned human (US-9.4) receives a generated one-time password
-- and is flagged to change it on first login; the same flag is set when an
-- admin resets a password. US-9.5 enforces the flag app-wide. Agents have no
-- password, so the flag only ever applies to human principals.
alter table public.principals
  add column must_change_password boolean not null default false;
