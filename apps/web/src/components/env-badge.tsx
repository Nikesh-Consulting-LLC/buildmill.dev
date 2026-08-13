import { envLabel } from "@/lib/env-label";
import { cn } from "@/lib/utils";

// US-14.11: amber environment chip next to the logo; renders nothing on prod.
export function EnvBadge({ className }: { className?: string }) {
  const label = envLabel();
  if (!label) return null;
  return (
    <span
      className={cn(
        "rounded-sm border border-amber-500/40 bg-amber-500/15 px-1 text-[10px] font-semibold tracking-wider text-amber-700 dark:text-amber-400",
        className
      )}
    >
      {label}
    </span>
  );
}
