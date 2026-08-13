import { AlertTriangle, Database, Gauge } from "lucide-react";
import { Badge } from "@/components/ui/badge";

export type IssueComplexity = {
  complexity: string | null;
  touches_critical: boolean | null;
  data_model_impact: string | null;
  complexity_rationale?: string | null;
  complexity_basis?: string | null;
  complexity_model?: string | null;
};

const LEVEL_STYLE: Record<string, string> = {
  trivial: "border-slate-200 bg-slate-100 text-slate-700 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-300",
  low: "border-emerald-200 bg-emerald-100 text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950 dark:text-emerald-300",
  medium: "border-amber-200 bg-amber-100 text-amber-800 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-300",
  high: "border-red-200 bg-red-100 text-red-800 dark:border-red-900 dark:bg-red-950 dark:text-red-300",
};

const LEVEL_LABEL: Record<string, string> = {
  trivial: "Trivial",
  low: "Low",
  medium: "Medium",
  high: "High",
};

const DATA_MODEL_LABEL: Record<string, string> = {
  none: "No data model change",
  backward_compatible: "Backward-compatible",
  needs_migration: "Needs migration",
};

/** US-7.1: the compact complexity badge for triage surfaces (list/board).
 * Absent when not scored. */
export function ComplexityBadge({ complexity }: { complexity: string | null }) {
  if (!complexity) return null;
  return (
    <Badge
      className={`gap-1 font-normal ${LEVEL_STYLE[complexity] ?? LEVEL_STYLE.low}`}
      title={`Complexity: ${LEVEL_LABEL[complexity] ?? complexity} (advisory)`}
    >
      <Gauge className="size-3" />
      {LEVEL_LABEL[complexity] ?? complexity}
    </Badge>
  );
}

/** US-7.1: the full advisory read-out on detail + plan-review surfaces. */
export function ComplexityDetail({ estimate }: { estimate: IssueComplexity }) {
  if (!estimate.complexity) return null;
  const source =
    estimate.complexity_basis === "plan"
      ? "refined from the plan"
      : "estimated from the story";
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs">
      <ComplexityBadge complexity={estimate.complexity} />
      {estimate.touches_critical && (
        <Badge
          variant="outline"
          className="gap-1 border-red-200 font-normal text-red-700 dark:border-red-900 dark:text-red-300"
          title="Touches RLS / auth / secrets / security-definer SQL"
        >
          <AlertTriangle className="size-3" />
          Touches critical
        </Badge>
      )}
      {estimate.data_model_impact && (
        <Badge variant="outline" className="gap-1 font-normal">
          <Database className="size-3" />
          {DATA_MODEL_LABEL[estimate.data_model_impact] ??
            estimate.data_model_impact}
        </Badge>
      )}
      {/* US-24.5: the rationale is a sentence, not a chip. Inline among the
          badges it wraps into whatever gap is left and the row reads as one
          run-on line; `basis-full` puts it on its own line every time,
          whatever the badge widths or the viewport. Same for the advisory
          note, which then loses the leading separator it only needed when it
          trailed something. */}
      {estimate.complexity_rationale && (
        <span className="basis-full text-muted-foreground italic">
          {estimate.complexity_rationale}
        </span>
      )}
      <span className="basis-full text-muted-foreground">
        advisory — {source}
        {estimate.complexity_model ? ` by ${estimate.complexity_model}` : ""}
      </span>
    </div>
  );
}
