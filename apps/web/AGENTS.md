<!-- BEGIN:nextjs-agent-rules -->
# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` before writing any code. Heed deprecation notices.
<!-- END:nextjs-agent-rules -->

# Divergence cheatsheet

The patterns below cover what actually differs from training data in this app
(next `16.2.10`, React `19.2.4`, Tailwind v4, `@base-ui/react` `1.6.0`). Check
here first; fall back to `node_modules/next/dist/docs/` only for App Router
APIs this sheet doesn't cover.

## Next 16

- **`src/proxy.ts` replaces `middleware.ts`.** It exports an async `proxy(request: NextRequest)`
  function; ours refreshes the Supabase session via `@supabase/ssr` and gates non-public paths.
  Never create a `middleware.ts`.
- App Router APIs (`params`, `searchParams`, `cookies()`, `headers()`) are **async** — `await` them.

## shadcn/ui is Base UI, not Radix

Components live in `src/components/ui/`. The API differences that bite:

- **No `asChild`.** Compose triggers with the `render` prop:
  `<DialogPrimitive.Close render={<Button variant="outline" />}>Close</DialogPrimitive.Close>`
  (see `dialog.tsx`, `select.tsx`, `dropdown-menu.tsx`).
- **`Select` needs `items`.** Pass the option list to `Select` itself (`items={...}`) or the
  closed trigger renders a value, not a label.
- **Reuse the shared components** before writing new ones: `StatusBadge` (`status-badge.tsx`)
  for any task/run status, `EmptyState` (`empty-state.tsx`) for any empty view,
  `ConfirmDialog` (`confirm-dialog.tsx`) for destructive confirmations.

## Tailwind v4

- **There is no `tailwind.config.*`.** Configuration is CSS-first in `src/app/globals.css`
  (`@import "tailwindcss"` + `@theme` blocks). Design tokens (`--background`, `--primary`,
  `--muted`, `--border`, …) are defined there — style with tokens, never hard-coded colors.

## Supabase

- Browser components use `src/lib/supabase/client.ts`; server components/actions use
  `server.ts`. Types come from `database.types.ts` — **generated, never hand-edited**.
- Plain CRUD goes straight to Supabase under RLS ("build less API"); the FastAPI backend is
  only for orchestration.
- **Name the FK relationship in any embed** where two tables are reachable more than one way:
  `projects!deployments_project_id_org_id_fkey(name)`, not `projects(name)` — un-hinted
  embeds start answering `300 Multiple Choices` (PGRST201) the moment a junction table lands.

## Tests

`npm run test:web` runs Node's built-in runner over `src/**/*.test.ts`
(`node --experimental-strip-types --test`). Add a `.test.ts` beside any pure logic you
change — coverage is sparse and only grows this way. `npm run build` type-checks; run it
before committing web changes.
