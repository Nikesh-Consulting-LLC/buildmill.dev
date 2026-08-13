// US-62.7: a route TEMPLATE, not the raw interpolated path — `/issues/:id`,
// not `/issues/<uuid>` — so performance rows aggregate instead of
// fragmenting one-per-record. The App Router doesn't hand a client
// component its matched route pattern, so this normalizes the one shape
// that dominates this app's URLs (a bare UUID path segment) instead of
// needing that plumbing.

const UUID_SEGMENT = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function normalizeRoute(pathname: string): string {
  const normalized = pathname
    .split("/")
    .map((segment) => (UUID_SEGMENT.test(segment) ? ":id" : segment))
    .join("/");
  return normalized || "/";
}
