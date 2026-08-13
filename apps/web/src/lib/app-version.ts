// US-51.1: which build is this? NEXT_PUBLIC_APP_VERSION is inlined at build
// time from apps/web/VERSION (see next.config.ts); a checkout without the
// stamp is by definition a dev build.

/**
 * Display form of a `git describe` string: the release version, with drift
 * from it compressed to a count — `2026.07.29.1-7-g3147da2` → `2026.07.29.1
 * +7`. Exact tags and the bare-SHA fallback pass through untouched.
 */
export function formatVersion(raw: string): string {
  return raw.replace(/-(\d+)-g[0-9a-f]+$/, " +$1");
}

/** Version to display under the logo, e.g. "2026.07.29.1 +7"; "dev" locally. */
export function appVersion(): string {
  const raw = process.env.NEXT_PUBLIC_APP_VERSION?.trim();
  return raw ? formatVersion(raw) : "dev";
}
