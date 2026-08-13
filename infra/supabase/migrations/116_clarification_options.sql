-- US-14.9: an agent's question can offer choices.
--
-- `request_clarification` took one string, so the hub rendered one empty
-- box. The round-3 breakdown run asked a question containing two stack
-- decisions, each with an obvious short answer, as 1,184 characters of
-- prose above a blank textarea — and any sloppiness in the reply became a
-- wrong assumption baked into nine stories.
--
-- Additive on purpose. `options` null means exactly today's behaviour, so
-- every stored question and every existing caller keeps working: the MCP
-- arguments are optional, and the UI falls back to the textarea.
--
-- `answer` keeps carrying free text. An answer may be choices, prose, or
-- both — the free-text box never goes away, because a manager must always
-- be able to say "none of these, do X instead".

alter table public.clarifications
  add column if not exists options jsonb,
  add column if not exists multi_select boolean not null default false,
  add column if not exists selected_options jsonb;

comment on column public.clarifications.options is
  'US-14.9: [{label, description}] the asking agent offers. Null = a '
  'free-text question, exactly as before.';
comment on column public.clarifications.multi_select is
  'US-14.9: whether the manager may choose more than one option.';
comment on column public.clarifications.selected_options is
  'US-14.9: the labels chosen. Free text still lands in answer, so an '
  'answer may be choices, prose, or both.';

-- At least two options (one is not a choice) and a sane ceiling, so a
-- malformed set is refused at the source rather than rendered as a broken
-- form. Checked here as well as in the MCP tool: the tool gives the agent
-- a reason it can act on, the constraint makes the rule true regardless
-- of which path writes.
alter table public.clarifications
  drop constraint if exists clarifications_options_shape;
alter table public.clarifications
  add constraint clarifications_options_shape check (
    options is null
    or (
      jsonb_typeof(options) = 'array'
      and jsonb_array_length(options) between 2 and 6
    )
  );
