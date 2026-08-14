// US-51.1 / US-91.16: which build is this? NEXT_PUBLIC_APP_VERSION is inlined
// at build time from apps/web/VERSION (see next.config.ts); a checkout without
// the stamp is by definition a dev build.
//
// US-91.16: the stamp used to be a bare `git describe` string, which is a fact
// about TAGS, not about this build. Four deploys off one tag produced four
// identical footers, and with no tag at all it fell back to a bare sha that
// looked like a version. It now carries the two things that are always true —
// the commit and when it was built — and lets the version ride along when a
// tag exists to supply one.

export type BuildStamp = {
  /** `git describe` output, when a version tag is reachable. */
  version: string | null;
  /** Full commit sha. */
  commit: string | null;
  /** Branch deployed. */
  ref: string | null;
  /** ISO-8601 UTC. */
  builtAt: string | null;
  /** Commits since the named version tag, when there is one to drift from. */
  drift: number | null;
};

const EMPTY: BuildStamp = {
  version: null,
  commit: null,
  ref: null,
  builtAt: null,
  drift: null,
};

/**
 * Display form of a `git describe` string: the release version, with drift
 * from it compressed to a count — `2026.07.29.1-7-g3147da2` → `2026.07.29.1
 * +7`. Exact tags and the bare-SHA fallback pass through untouched.
 */
export function formatVersion(raw: string): string {
  return raw.replace(/-(\d+)-g[0-9a-f]+$/, " +$1");
}

/** A `git describe` string that names a tag, split into tag and drift. */
function splitDescribe(raw: string): { tag: string; drift: number | null } {
  const m = /^(.*)-(\d+)-g[0-9a-f]+$/.exec(raw);
  if (m) return { tag: m[1], drift: Number(m[2]) };
  return { tag: raw, drift: 0 };
}

/**
 * Parse the deploy stamp. Accepts the `key=value` lines the workflows write
 * and, for tolerance, a bare `git describe` line (what older builds carry).
 * Anything unparseable degrades to a dev build rather than a broken string.
 */
export function parseStamp(raw: string | undefined | null): BuildStamp {
  const text = raw?.trim();
  if (!text) return EMPTY;

  if (text.includes("=")) {
    const fields: Record<string, string> = {};
    for (const line of text.split(/\r?\n/)) {
      const i = line.indexOf("=");
      if (i <= 0) continue;
      const value = line.slice(i + 1).trim();
      if (value) fields[line.slice(0, i).trim()] = value;
    }
    const described = fields.version ?? "";
    // `--always` with no matching tag answers a bare sha; that is a commit,
    // not a version, and must never be shown as one.
    const named =
      described && !/^[0-9a-f]{7,40}$/.test(described)
        ? splitDescribe(described)
        : null;
    return {
      version: named?.tag ?? null,
      commit: fields.commit ?? null,
      ref: fields.ref ?? null,
      builtAt: fields.built_at ?? null,
      drift: named?.drift ?? null,
    };
  }

  if (/^[0-9a-f]{7,40}$/.test(text))
    return { ...EMPTY, commit: text };
  const named = splitDescribe(text);
  return { ...EMPTY, version: named.tag, drift: named.drift };
}

/** `Aug 14, 2026` — the date alone. The manager's note (2026-08-14): a
 *  footer answers "which day's build", not "which minute's"; the exact UTC
 *  timestamp stays one hover away in `versionDetail`. Pinned to en-US so the
 *  server-rendered footer reads the same on every machine. */
export function formatBuiltAt(iso: string | null): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

/**
 * The compact line under the logo.
 *
 * Tagged: `2026.08.14.1 +3 · Aug 14, 2026`. Untagged: just `Aug 14, 2026` —
 * amended after UAT, because a bare sha is noise to the person reading a
 * footer: it answers "which build" only for someone already holding a list
 * of shas, while the build date answers "is this today's" for everyone. The
 * sha and the exact time are still one hover away in `versionDetail`, so
 * nothing is lost for the case that needs them. `dev` when there is no
 * stamp at all.
 */
export function appVersion(raw?: string | null): string {
  const stamp = parseStamp(
    raw === undefined ? process.env.NEXT_PUBLIC_APP_VERSION : raw
  );
  const when = formatBuiltAt(stamp.builtAt);
  const name = stamp.version
    ? stamp.drift
      ? `${stamp.version} +${stamp.drift}`
      : stamp.version
    : null;
  if (name) return when ? `${name} · ${when}` : name;
  if (when) return when;
  // No version and no timestamp: the sha is all there is, so it beats "dev".
  return stamp.commit ? `commit ${stamp.commit.slice(0, 7)}` : "dev";
}

/** The full detail, one hover away: sha, branch, exact UTC timestamp. */
export function versionDetail(raw?: string | null): string | undefined {
  const stamp = parseStamp(
    raw === undefined ? process.env.NEXT_PUBLIC_APP_VERSION : raw
  );
  const parts: string[] = [];
  if (stamp.version)
    parts.push(
      stamp.drift
        ? `${stamp.version}, ${stamp.drift} commit${stamp.drift === 1 ? "" : "s"} since`
        : stamp.version
    );
  if (stamp.commit) parts.push(`commit ${stamp.commit}`);
  if (stamp.ref) parts.push(`branch ${stamp.ref}`);
  if (stamp.builtAt) parts.push(`built ${stamp.builtAt}`);
  return parts.length ? parts.join(" · ") : undefined;
}
