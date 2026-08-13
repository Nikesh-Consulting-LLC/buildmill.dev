-- 036: fix US-2.11 search — PostgREST or-filters cannot cast
-- (acceptance_criteria::text.ilike... fails the logic-tree parser), so
-- materialize the searchable text as a generated column instead.
alter table public.issues
  add column if not exists search_text text
  generated always as (
    coalesce(title, '') || ' ' || coalesce(body, '')
      || ' ' || coalesce(acceptance_criteria::text, '')
  ) stored;

comment on column public.issues.search_text is
  'Generated: title + body + acceptance criteria text, for ilike search (US-2.11).';
