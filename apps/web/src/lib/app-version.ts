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

/** `14 Aug 09:12` — short, local, and enough to answer "is this today's". */
export function formatBuiltAt(iso: string | null): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleString(undefined, {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/**
 * The compact line under the logo: `Build 2026.08.14.1 · 14 Aug 09:12`, or
 * `Build 0230b43 · 14 Aug 09:12` for an untagged build — where the short sha
 * is plainly a commit, never left looking like a version. `dev` locally.
 */
export function appVersion(raw?: string | null): string {
  const stamp = parseStamp(
    raw === undefined ? process.env.NEXT_PUBLIC_APP_VERSION : raw
  );
  const name = stamp.version
    ? stamp.drift
      ? `${stamp.version} +${stamp.drift}`
      : stamp.version
    : stamp.commit
      ? `commit ${stamp.commit.slice(0, 7)}`
      : null;
  if (!name) return "dev";
  const when = formatBuiltAt(stamp.builtAt);
  return when ? `${name} · ${when}` : name;
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
