import { AgentText } from "@/components/agent-text";

// US-13.3: what the agent wanted the manager to know at hand-back —
// carried by the submission itself, shown where the decision is made.
//
// US-14.1: rendered as the markdown it was written in. No clamp here —
// this is the gate, so the manager reads all of it before deciding.
export function AgentNotes({ notes }: { notes: string | null | undefined }) {
  if (!notes?.trim()) return null;
  return (
    <div className="rounded-md border border-sky-200 bg-sky-50 px-3 py-2 text-sm text-sky-900 dark:border-sky-900 dark:bg-sky-950 dark:text-sky-200">
      <p className="font-medium">What the agent wants you to know</p>
      <AgentText className="mt-1">{notes}</AgentText>
    </div>
  );
}
