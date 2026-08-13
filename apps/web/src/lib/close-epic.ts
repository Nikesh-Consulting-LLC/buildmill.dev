import { createClient } from "@/lib/supabase/client";

type Client = ReturnType<typeof createClient>;

/**
 * US-71.1: close an epic in one place. Sets the status plus the completion
 * stamps the activity feed reads, and drops `active` in the same update —
 * the `epics_active_open` check constraint forbids a completed epic staying
 * active, which is why completing the active epic used to error. The
 * `epics_guard_completion` trigger stays the enforcement against closing
 * over open work; its message comes back as the returned error string.
 *
 * When the closed epic *was* active, the newest remaining open epic is
 * promoted so new work items keep a sensible default (`assign_issue_number`
 * refuses an epic-less insert in a project with no active epic).
 */
export async function closeEpic(
  supabase: Client,
  epic: { id: string; projectId: string; active: boolean }
): Promise<string | null> {
  const {
    data: { session },
  } = await supabase.auth.getSession();

  const { error } = await supabase
    .from("epics")
    .update({
      status: "completed",
      active: false,
      completed_at: new Date().toISOString(),
      completed_by: session?.user.id ?? null,
    })
    .eq("id", epic.id);
  if (error) return error.message;

  if (epic.active) {
    const { data: next } = await supabase
      .from("epics")
      .select("id")
      .eq("project_id", epic.projectId)
      .eq("status", "open")
      .order("number", { ascending: false })
      .limit(1)
      .maybeSingle();
    if (next) {
      const { error: promoteError } = await supabase
        .from("epics")
        .update({ active: true })
        .eq("id", next.id);
      if (promoteError) return promoteError.message;
    }
  }
  return null;
}

/** Reopen a closed epic, clearing the stamps closing set. `active` stays
 * false — the project's active epic (if any) keeps the default. */
export async function reopenEpic(
  supabase: Client,
  epicId: string
): Promise<string | null> {
  const { error } = await supabase
    .from("epics")
    .update({ status: "open", completed_at: null, completed_by: null })
    .eq("id", epicId);
  return error?.message ?? null;
}
