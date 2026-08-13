/** Compact relative timestamp for "last seen"-style displays. */
export function formatLastSeen(iso: string | null) {
  if (!iso) return "never";
  const delta = Date.now() - new Date(iso).getTime();
  if (delta < 60_000) return "just now";
  if (delta < 3_600_000) return `${Math.floor(delta / 60_000)} min ago`;
  if (delta < 86_400_000) return `${Math.floor(delta / 3_600_000)} h ago`;
  return new Date(iso).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}
