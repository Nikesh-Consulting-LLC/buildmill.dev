-- 062_help_content_rpc: everyone reads, only the superadmin writes
-- (US-2.30).
--
-- The /help page's descriptive text resolves from help/* keys in
-- llm_prompt_templates (us-5.17). Factory defaults are code-owned (API
-- help_content.py, mirrored in the web's help-content.ts), so this RPC
-- returns only the OVERRIDE rows — the web merges override-else-default.
-- Scoped strictly to help/%: thinking-prompt and worker rows never leak
-- through it, and the table itself stays default-deny.

create or replace function public.help_content_overrides()
returns table (prompt_key text, content text)
language sql
stable
security definer
set search_path = public
as $$
  select prompt_key, content
  from public.llm_prompt_templates
  where prompt_key like 'help/%'
    and nullif(trim(content), '') is not null;
$$;

revoke all on function public.help_content_overrides() from public, anon;
grant execute on function public.help_content_overrides() to authenticated;
