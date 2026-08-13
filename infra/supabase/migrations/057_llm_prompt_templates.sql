-- 057_llm_prompt_templates: superadmin-managed prompt & content templates
-- (US-5.17).
--
-- One platform-scoped table for every template the factory serves:
-- thinking-function prompts (bare function keys, defaults in llm.py),
-- worker-instruction factory defaults (worker_instruction/<kind>, baked
-- here), and guideline-section content defaults (guideline_section/<key>,
-- baked here). Absence of a row (or blank content) means factory default —
-- reset is a delete; defaults are never copied into rows.
--
-- Deliberately NO client RLS policies (default-deny, like the servers
-- bucket): all access goes through superadmin-gated API endpoints and the
-- API's direct Postgres connection. The only client-reachable surfaces are
-- the security definer functions below, which expose exactly the
-- guideline-section and worker-instruction EFFECTIVE texts — never a
-- thinking-prompt override.

create table public.llm_prompt_templates (
  id uuid primary key default gen_random_uuid(),
  prompt_key text not null unique,
  content text not null,
  updated_by uuid,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

alter table public.llm_prompt_templates enable row level security;
-- no policies: default-deny for every client role

create trigger llm_prompt_templates_updated_at
  before update on public.llm_prompt_templates
  for each row execute function public.touch_updated_at();

-- Override lookup shared by the functions below. SECURITY DEFINER so the
-- default-deny table is readable from client-invoked definer functions.
create or replace function public.prompt_template_override(p_key text)
returns text
language sql
stable
security definer
set search_path = public
as $$
  select nullif(trim(content), '')
  from public.llm_prompt_templates
  where prompt_key = p_key;
$$;

-- ------------------------------------------- worker-instruction defaults
-- The baked factory texts (canonical source moved here from migration 052).
create or replace function public.baked_worker_instruction(p_kind text)
returns text
language sql
immutable
as $$
  select case p_kind
    when 'prd' then
      'Write a product requirements document for this feature from the raw '
      || 'idea and context provided. Produce exactly these four markdown '
      || 'sections, in this order: ## Problem, ## Goals, ## Out of scope, '
      || '## Acceptance criteria. Be concrete and testable in the '
      || 'acceptance criteria; keep scope honest — anything doubtful goes '
      || 'to Out of scope. If this is a redraft, address the send-back '
      || 'feedback directly instead of starting over.'
    when 'plan' then
      'Study the repository first, then produce a plan — not code. Do not '
      || 'modify any project file. Write an implementation plan (approach, '
      || 'files to touch, risks) and a test plan (how the change will be '
      || 'verified). Propose concrete test cases where useful. Honor the '
      || 'acceptance criteria and the PRD context when present; if this is '
      || 'a re-plan, address the send-back feedback.'
    when 'code' then
      'Implement the change honoring the approved implementation plan and '
      || 'the acceptance criteria. Follow the project guidelines and '
      || 'learnings. Keep the diff focused — no drive-by refactors. Note '
      || 'test cases a human should run when submitting. If this is a '
      || 'retry, address the rejection feedback directly.'
    else null
  end;
$$;

-- default_worker_instruction v2: the superadmin override, else the baked
-- text. SECURITY DEFINER so the web UI's Reset-to-default RPC (us-5.14)
-- sees the override through the default-deny table. Seeding, blank-content
-- fallback (worker_instruction_for), and reset all flow through here.
create or replace function public.default_worker_instruction(p_kind text)
returns text
language sql
stable
security definer
set search_path = public
as $$
  select coalesce(
    public.prompt_template_override('worker_instruction/' || p_kind),
    public.baked_worker_instruction(p_kind)
  );
$$;

-- -------------------------------------------- guideline-section defaults
-- Baked content skeletons for every catalog section; buildmill-workflow
-- delegates to its migration-055 source of truth.
create or replace function public.baked_guideline_section(p_key text)
returns text
language sql
stable
as $$
  select case p_key
    when 'buildmill-workflow' then public.default_buildmill_workflow_section()
    when 'tech-stack' then
      E'- Languages: …\n- Frameworks: …\n- Key libraries (versions where they matter): …'
    when 'commands' then
      E'- Install: `…`\n- Dev server: `…`\n- Build: `…`\n- Test: `…`\n- Lint: `…`'
    when 'code-style' then
      E'- Naming: …\n- Formatting: …\n- Preferred patterns a linter cannot enforce: …'
    when 'things-to-avoid' then
      E'- Known footguns: …\n- Deprecated patterns: …\n- Files not to touch: …'
    when 'overview' then
      'What the project is, who uses it, and the domain terms a fresh session should not have to guess: …'
    when 'architecture' then
      E'- How the pieces fit: …\n- Where core logic lives: …\n- Non-obvious design decisions: …'
    when 'file-structure' then
      E'- Layout worth knowing: …\n- Where new code usually goes: …'
    when 'testing' then
      E'- How to run tests: `…`\n- What must be tested: …\n- Coverage expectations: …'
    when 'environment' then
      E'- Required env vars: …\n- Secrets handling: …\n- Local quirks (ports, docker, seed data): …'
    when 'git-pr' then
      E'- Branch naming: …\n- Commit format: …\n- PRs vs direct push: …'
    when 'monorepo' then
      E'- Commands that run at the repo root: …\n- Commands that run inside a package: …'
    when 'doc-links' then
      '- [Document name](url) — what it covers'
    when 'known-issues' then
      '- Module or area: … — why it is in flux and what not to "fix"'
    when 'boundaries' then
      E'- Never modify: …\n- Ask before: …'
    when 'preferred-libs' then
      '- Use … not … (why)'
    when 'good-patterns' then
      '- `path/to/file` — reference implementation of …'
    when 'agent-workflows' then
      '- Command or workflow: … — when to use it'
    else null
  end;
$$;

-- Effective content for one section: override, else baked.
create or replace function public.effective_guideline_section(p_key text)
returns text
language sql
stable
security definer
set search_path = public
as $$
  select coalesce(
    public.prompt_template_override('guideline_section/' || p_key),
    public.baked_guideline_section(p_key)
  );
$$;

-- Client RPC for the Add-section pre-fill: effective defaults for every
-- catalog section — and nothing else from the table.
create or replace function public.guideline_section_defaults()
returns table (section_key text, content text)
language sql
stable
security definer
set search_path = public
as $$
  select k.key, public.effective_guideline_section(k.key)
  from unnest(array[
    'tech-stack', 'commands', 'code-style', 'things-to-avoid', 'overview',
    'architecture', 'file-structure', 'testing', 'environment', 'git-pr',
    'monorepo', 'doc-links', 'known-issues', 'boundaries', 'preferred-libs',
    'good-patterns', 'agent-workflows', 'buildmill-workflow'
  ]) as k(key);
$$;

revoke all on function public.prompt_template_override(text) from public, anon;
revoke all on function public.effective_guideline_section(text) from public, anon;
revoke all on function public.guideline_section_defaults() from public, anon;
grant execute on function public.guideline_section_defaults() to authenticated;
grant execute on function public.effective_guideline_section(text) to authenticated;

-- us-5.13's new-project seed now honors the superadmin's Build Mill text.
create or replace function public.seed_buildmill_guidelines_section()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into public.project_guidelines
    (org_id, project_id, section_key, title, content, sort_order)
  values
    (new.org_id, new.id, 'buildmill-workflow', 'Working with Build Mill',
     public.effective_guideline_section('buildmill-workflow'), 999)
  on conflict (project_id, section_key) where section_key <> 'custom'
  do nothing;
  return new;
end;
$$;
