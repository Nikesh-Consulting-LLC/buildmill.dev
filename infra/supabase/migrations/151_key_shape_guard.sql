-- 151_key_shape_guard: an API key that looks like an email is refused
-- (US-31.4).
--
-- Browser autofill put `kaushlesh@nikesh.llc` into the provider dialog's API
-- key field and it was saved — write-only, into Vault, where nothing could
-- read it back to disprove (`Key set · ends in ····.llc`). Every gateway
-- call keyed on it then fails as an auth error that reads like a routing
-- problem. The model-id field got this guard in US-27.8; the field next to
-- it never did.
--
-- The guard is deliberately minimal: no provider key from any vendor
-- contains whitespace or '@'. Anything stricter would eventually refuse a
-- real key from a vendor we have not met.

create or replace function public.set_llm_provider_key(p_provider uuid, p_key text)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  v_org uuid;
  v_secret_id uuid;
begin
  select org_id, vault_secret_id into v_org, v_secret_id
  from public.llm_providers where id = p_provider;
  -- unchanged from 045: existence and membership answer identically, so the
  -- RPC never confirms a provider id to a non-member.
  if v_org is null or not public.is_org_member(v_org) then
    raise exception 'not authorized';
  end if;
  if p_key is null or length(p_key) < 4 then
    raise exception 'invalid key';
  end if;
  -- US-31.4: shape guard — an email or anything with spaces is not an API
  -- key from any provider. The named refusal beats a stored key that fails
  -- every call while claiming to be set.
  if p_key ~ '\s' then
    raise exception 'that does not look like an API key (it contains whitespace)';
  end if;
  if position('@' in p_key) > 0 then
    raise exception 'that looks like an email address, not an API key — check for browser autofill';
  end if;

  if v_secret_id is null then
    v_secret_id := vault.create_secret(p_key, 'llm_api_key:' || p_provider::text);
  else
    perform vault.update_secret(v_secret_id, p_key);
  end if;

  update public.llm_providers
  set vault_secret_id = v_secret_id,
      key_last4 = right(p_key, 4)
  where id = p_provider;
end;
$$;
