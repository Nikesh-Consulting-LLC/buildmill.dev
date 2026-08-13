import { cn } from "@/lib/utils";

export type IssueStatus =
  | "draft"
  | "prd-review"
  | "ready"
  | "planning"
  | "plan-review"
  | "planned"
  | "queued"
  | "running"
  | "needs-fixes"
  | "in-review"
  | "merged"
  | "done"
  | "succeeded"
  | "cancelled"
  // US-33.2, retained by US-37.2: a run stopped rather than failed. Nothing
  // produces this any more — its only producer was the per-run spend ceiling,
  // now replaced by a project budget checked before a run is created — but runs
  // stopped before that change still exist and must still render.
  | "stopped"
  | "failed"
  // US-21.1: release statuses. Same badge, so a release reads like everything
  // else in the app and never renders a raw slug.
  // US-63.2: notes-ready/deploying/uat-deploy-failed split "one agent job"
  // into "an agent wrote notes" and "the system is deploying it" — two
  // states that used to be invisible inside a single 'running'.
  | "notes-ready"
  | "deploying"
  | "uat-deploy-failed"
  | "uat-deployed"
  | "uat-signed-off"
  | "promoting"
  | "released"
  | "rolled-back"
  | "rejected";

const STATUS_STYLES: Record<IssueStatus, { label: string; className: string }> = {
  draft: {
    label: "Draft",
    className:
      "bg-muted text-muted-foreground border-transparent",
  },
  "prd-review": {
    label: "PRD review",
    className:
      "bg-sky-100 text-sky-700 border-sky-200 dark:bg-sky-950 dark:text-sky-300 dark:border-sky-900",
  },
  ready: {
    label: "Ready",
    className:
      "bg-teal-100 text-teal-700 border-teal-200 dark:bg-teal-950 dark:text-teal-300 dark:border-teal-900",
  },
  planning: {
    label: "Planning",
    className:
      "bg-amber-100 text-amber-700 border-amber-200 dark:bg-amber-950 dark:text-amber-300 dark:border-amber-900",
  },
  "plan-review": {
    label: "Plan review",
    className:
      "bg-violet-100 text-violet-700 border-violet-200 dark:bg-violet-950 dark:text-violet-300 dark:border-violet-900",
  },
  planned: {
    label: "Planned",
    className:
      "bg-blue-100 text-blue-700 border-blue-200 dark:bg-blue-950 dark:text-blue-300 dark:border-blue-900",
  },
  queued: {
    label: "Queued",
    className:
      "bg-blue-100 text-blue-700 border-blue-200 dark:bg-blue-950 dark:text-blue-300 dark:border-blue-900",
  },
  running: {
    label: "Running",
    className:
      "bg-amber-100 text-amber-700 border-amber-200 dark:bg-amber-950 dark:text-amber-300 dark:border-amber-900",
  },
  "needs-fixes": {
    label: "Needs fixes",
    className:
      "bg-orange-100 text-orange-700 border-orange-200 dark:bg-orange-950 dark:text-orange-300 dark:border-orange-900",
  },
  "in-review": {
    label: "In review",
    className:
      "bg-violet-100 text-violet-700 border-violet-200 dark:bg-violet-950 dark:text-violet-300 dark:border-violet-900",
  },
  merged: {
    label: "Merged",
    className:
      "bg-emerald-100 text-emerald-700 border-emerald-200 dark:bg-emerald-950 dark:text-emerald-300 dark:border-emerald-900",
  },
  done: {
    label: "Done",
    className:
      "bg-emerald-100 text-emerald-700 border-emerald-200 dark:bg-emerald-950 dark:text-emerald-300 dark:border-emerald-900",
  },
  succeeded: {
    label: "Succeeded",
    className:
      "bg-emerald-100 text-emerald-700 border-emerald-200 dark:bg-emerald-950 dark:text-emerald-300 dark:border-emerald-900",
  },
  cancelled: {
    label: "Cancelled",
    className:
      "bg-slate-100 text-slate-600 border-slate-200 dark:bg-slate-900 dark:text-slate-400 dark:border-slate-800",
  },
  stopped: {
    label: "Stopped",
    className:
      "bg-amber-100 text-amber-700 border-amber-200 dark:bg-amber-950 dark:text-amber-300 dark:border-amber-900",
  },
  failed: {
    label: "Failed",
    className:
      "bg-red-100 text-red-700 border-red-200 dark:bg-red-950 dark:text-red-300 dark:border-red-900",
  },
  "notes-ready": {
    label: "Notes ready",
    className:
      "bg-blue-100 text-blue-700 border-blue-200 dark:bg-blue-950 dark:text-blue-300 dark:border-blue-900",
  },
  deploying: {
    label: "Deploying",
    className:
      "bg-amber-100 text-amber-700 border-amber-200 dark:bg-amber-950 dark:text-amber-300 dark:border-amber-900",
  },
  "uat-deploy-failed": {
    label: "UAT deploy failed",
    className:
      "bg-red-100 text-red-700 border-red-200 dark:bg-red-950 dark:text-red-300 dark:border-red-900",
  },
  "uat-deployed": {
    label: "On UAT",
    className:
      "bg-sky-100 text-sky-700 border-sky-200 dark:bg-sky-950 dark:text-sky-300 dark:border-sky-900",
  },
  "uat-signed-off": {
    label: "Signed off",
    className:
      "bg-teal-100 text-teal-700 border-teal-200 dark:bg-teal-950 dark:text-teal-300 dark:border-teal-900",
  },
  promoting: {
    label: "Promoting",
    className:
      "bg-amber-100 text-amber-700 border-amber-200 dark:bg-amber-950 dark:text-amber-300 dark:border-amber-900",
  },
  released: {
    label: "Released",
    className:
      "bg-emerald-100 text-emerald-700 border-emerald-200 dark:bg-emerald-950 dark:text-emerald-300 dark:border-emerald-900",
  },
  "rolled-back": {
    label: "Rolled back",
    className:
      "bg-orange-100 text-orange-700 border-orange-200 dark:bg-orange-950 dark:text-orange-300 dark:border-orange-900",
  },
  rejected: {
    label: "Rejected",
    className:
      "bg-red-100 text-red-700 border-red-200 dark:bg-red-950 dark:text-red-300 dark:border-red-900",
  },
};

// Callers cast raw database strings into IssueStatus (e.g. `last.status as
// IssueStatus`), so an unmapped value reaches this at runtime rather than
// failing the type check. Render it plainly instead of throwing.
const UNKNOWN_STATUS = {
  className: "bg-muted text-muted-foreground border-transparent",
};

export function StatusBadge({
  status,
  className,
}: {
  status: IssueStatus;
  className?: string;
}) {
  const style = STATUS_STYLES[status] ?? {
    label: status,
    className: UNKNOWN_STATUS.className,
  };
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium",
        style.className,
        className
      )}
    >
      {status === "running" && (
        <span className="relative flex size-1.5">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-amber-500 opacity-75" />
          <span className="relative inline-flex size-1.5 rounded-full bg-amber-500" />
        </span>
      )}
      {style.label}
    </span>
  );
}
