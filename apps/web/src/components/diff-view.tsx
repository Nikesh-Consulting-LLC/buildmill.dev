import { cn } from "@/lib/utils";

/** Render a unified diff, split per file, additions/removals colored. */
export function DiffView({ diff }: { diff: string }) {
  const files = diff
    .split(/^(?=diff --git )/m)
    .map((chunk) => chunk.trimEnd())
    .filter(Boolean);

  if (!files.length) {
    return <p className="text-sm text-muted-foreground">Empty diff.</p>;
  }

  return (
    <div className="grid gap-3">
      {files.map((file, i) => {
        const lines = file.split("\n");
        const header = lines[0].replace("diff --git ", "");
        return (
          // US-35.8: `min-w-0` for the same reason the tables needed it — the
          // `overflow-x-auto` below is real, but a grid item's default
          // `min-width: auto` grows this box to the widest diff line and the
          // scroll container never engages, pushing the page sideways instead.
          <details key={i} open className="min-w-0 rounded-md border">
            <summary className="cursor-pointer select-none truncate border-b bg-muted/40 px-3 py-1.5 font-mono text-xs font-medium">
              {header}
            </summary>
            <div className="overflow-x-auto">
              <pre className="p-0 text-xs leading-5">
                {lines.slice(1).map((line, j) => (
                  <div
                    key={j}
                    className={cn(
                      "px-3 whitespace-pre",
                      line.startsWith("+") &&
                        "bg-emerald-50 text-emerald-900 dark:bg-emerald-950/60 dark:text-emerald-200",
                      line.startsWith("-") &&
                        "bg-red-50 text-red-900 dark:bg-red-950/60 dark:text-red-200",
                      line.startsWith("@@") &&
                        "bg-blue-50 text-blue-800 dark:bg-blue-950/60 dark:text-blue-300"
                    )}
                  >
                    {line || " "}
                  </div>
                ))}
              </pre>
            </div>
          </details>
        );
      })}
    </div>
  );
}
