import type { SupabaseClient } from "@supabase/supabase-js";

export type ApprovalRow = {
  id: string;
  gate: string;
  decision: string;
  subject_type: string | null;
  subject_id: string | null;
  comment: string | null;
  actor: string | null;
  /** US-17.4/17.5: the project auto-approve setting cleared this gate (no human). */
  auto_approved?: boolean;
  created_at: string;
};

const ACRONYMS = new Set(["prd", "qa", "llm", "pr", "ci"]);

// Gate and decision labels are derived from the row's own string, never from a
// lookup of known gates, so a new gate renders correctly with no code change.
export function humanizeToken(token: string): string {
  const words = token.split("-").filter(Boolean);
  if (words.length === 0) return token;
  const rendered = words.map((w) =>
    ACRONYMS.has(w.toLowerCase()) ? w.toUpperCase() : w.toLowerCase()
  );
  if (!ACRONYMS.has(words[0].toLowerCase())) {
    rendered[0] = rendered[0].charAt(0).toUpperCase() + rendered[0].slice(1);
  }
  return rendered.join(" ");
}

export function subjectHref(
  subjectType: string | null,
  subjectId: string | null,
  issueId: string
): string | null {
  if (!subjectType || !subjectId) return null;
  switch (subjectType) {
    case "run":
      return `/review/${issueId}`;
    case "artifact":
      return `/issues/${issueId}#artifact-${subjectId}`;
    case "release_record":
      return `/issues/${issueId}#release`;
    default:
      return null;
  }
}

export function subjectLabel(
  subjectType: string | null,
  subjectId: string | null
): string | null {
  if (!subjectType || !subjectId) return null;
  return `${humanizeToken(subjectType)} ${subjectId.slice(0, 8)}`;
}

export function formatWhen(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** approvals.actor references auth.users(id), which PostgREST cannot embed
 * profiles across directly — a separate keyed read resolves display names. */
export async function fetchActorNames(
  supabase: SupabaseClient,
  actorIds: (string | null)[]
): Promise<Record<string, string>> {
  const ids = Array.from(
    new Set(actorIds.filter((id): id is string => Boolean(id)))
  );
  if (ids.length === 0) return {};

  const { data } = await supabase
    .from("profiles")
    .select("id, email, display_name")
    .in("id", ids);

  const names: Record<string, string> = {};
  for (const p of data ?? []) {
    names[p.id] = p.display_name || p.email || p.id;
  }
  return names;
}
