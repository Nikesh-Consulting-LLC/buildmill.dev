import { toast, toastError, toastSuccess } from "@/components/ui/toast";

/** US-15.1: the `docs_tree` outcome the PRD/plan approval endpoints return,
 * surfaced to the manager so an approval's repo write is never silent. Shapes
 * returned by `repo_docs.sync_tree` (via `_sync_docs_tree`):
 *   - success:   { commit_sha, files, unchanged? }
 *   - skipped:   { skipped }        — the tree is off, or there is no repo
 *   - failed:    { error, retry }   — GitHub unreachable / permissions / etc.
 *   - deferred:  { deferred }       — US-69.3: the write happens after the
 *                 response; its outcome lands on the issue timeline instead. */
export type DocsTreeOutcome =
  | {
      commit_sha?: string;
      files?: string[];
      unchanged?: boolean;
      skipped?: string;
      error?: string;
      deferred?: boolean;
    }
  | null
  | undefined;

/** Fire a toast describing what happened to the repo write. Safe to call
 * right before navigating away — the app-shell <Toaster/> outlives the route
 * change, so the confirmation survives the push. */
export function notifyDocsWrite(
  docs: DocsTreeOutcome,
  kind: "PRD" | "Plan"
): void {
  if (!docs) return;
  if (docs.deferred) {
    // The write is running in the background; the issue timeline will carry
    // the docs-written / docs-write-failed outcome. Nothing to toast.
    return;
  }
  if (docs.error) {
    toastError(
      `${kind} approved — but writing it to the repo failed`,
      `${docs.error} You can retry from the project's GitHub settings.`
    );
    return;
  }
  if (docs.skipped) {
    // "not enabled" is the manager's own opt-out — nothing to report. Any
    // other skip (e.g. no linked repository) is worth a quiet note.
    if (/not enabled/i.test(docs.skipped)) return;
    toast({
      title: `${kind} approved — repo docs not written`,
      description: docs.skipped,
      variant: "info",
    });
    return;
  }
  if (docs.unchanged) {
    toast({
      title: `${kind} approved`,
      description: "Repo docs already up to date.",
      variant: "info",
    });
    return;
  }
  if (docs.commit_sha) {
    toastSuccess(
      `${kind} approved and written to the repo`,
      `commit ${docs.commit_sha.slice(0, 7)}`
    );
  }
}
