import { cn } from "@/lib/utils";

/**
 * Brand-matched loading mark: the buildmill.dev arch stays put while a
 * single arrow — the logo's own arrow motif — travels left to right through
 * its mouth, disappearing off the right edge and reappearing on the left.
 */
export function BuildMillLoader({
  className,
  label = "Loading",
}: {
  className?: string;
  label?: string;
}) {
  return (
    <div
      role="status"
      aria-label={label}
      className={cn("flex flex-col items-center gap-3", className)}
    >
      <svg
        viewBox="0 0 320 240"
        className="h-24 w-auto motion-safe:animate-loader-glow"
        aria-hidden="true"
      >
        <defs>
          <clipPath id="bm-loader-track">
            <rect x="52" y="150" width="216" height="70" rx="6" />
          </clipPath>
        </defs>

        {/* arch */}
        <path
          d="M 60 150 A 100 100 0 0 1 260 150"
          fill="none"
          stroke="#0a2441"
          strokeWidth="44"
        />

        {/* single arrow, clipped to the arch's mouth, looping left to right */}
        <g clipPath="url(#bm-loader-track)">
          <path
            className="motion-safe:animate-loader-flow"
            d="M 34 174 H 138 V 158 L 190 185 L 138 212 L 152 185 L 138 196 H 34 Z"
            fill="#6a976e"
          />
        </g>
      </svg>
      <span className="text-xs font-medium text-muted-foreground">
        {label}…
      </span>
    </div>
  );
}
