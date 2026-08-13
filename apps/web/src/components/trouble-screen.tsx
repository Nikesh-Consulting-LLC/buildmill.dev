import Image from "next/image";
import type { LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * Shared presentation for the 404 and error boundaries (us-2.31) so the
 * four screens can't drift apart in style. Server-safe; also renders fine
 * when imported from a client error boundary.
 */
export function TroubleScreen({
  code,
  icon: Icon,
  headline,
  body,
  footnote,
  actions,
  showLogo = false,
  className,
}: {
  code?: string;
  icon?: LucideIcon;
  headline: string;
  body: string;
  footnote?: string;
  actions?: React.ReactNode;
  showLogo?: boolean;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center gap-6 p-6 text-center",
        className
      )}
    >
      {showLogo && (
        <div className="rounded-2xl bg-[#f6f6f6] px-4 py-3">
          {/* The lockup keeps its own light background so it stays legible in dark mode. */}
          <Image
            src="/buildmill-logo.png"
            alt="Build Mill logo"
            width={620}
            height={446}
            priority
            className="h-20 w-auto object-contain"
          />
        </div>
      )}
      {code ? (
        <div className="flex flex-col items-center gap-2" aria-hidden="true">
          <span className="text-7xl font-bold tracking-tight text-muted-foreground/30">
            {code}
          </span>
          <ConveyorLine />
        </div>
      ) : Icon ? (
        <div
          className="flex size-12 items-center justify-center rounded-full bg-muted"
          aria-hidden="true"
        >
          <Icon className="size-6 text-muted-foreground" />
        </div>
      ) : null}
      <div className="max-w-md space-y-2">
        <h1 className="text-lg font-semibold">{headline}</h1>
        <p className="text-sm text-muted-foreground">{body}</p>
      </div>
      {actions && (
        <div className="flex flex-wrap items-center justify-center gap-2">
          {actions}
        </div>
      )}
      {footnote && (
        <p className="text-xs text-muted-foreground/70">{footnote}</p>
      )}
    </div>
  );
}

function ConveyorLine() {
  return (
    <svg
      width="180"
      height="16"
      viewBox="0 0 180 16"
      className="text-muted-foreground/40"
      aria-hidden="true"
    >
      <line
        x1="2"
        y1="4"
        x2="178"
        y2="4"
        stroke="currentColor"
        strokeWidth="2"
        strokeDasharray="6 6"
        strokeLinecap="round"
        className="motion-safe:animate-belt"
      />
      <circle cx="30" cy="11" r="3" fill="currentColor" />
      <circle cx="90" cy="11" r="3" fill="currentColor" />
      <circle cx="150" cy="11" r="3" fill="currentColor" />
    </svg>
  );
}
