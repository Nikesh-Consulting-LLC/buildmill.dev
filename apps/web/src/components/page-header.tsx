/**
 * Phase 64: one header shape for every top-level page — title/description on
 * the left, page actions then the global project filter on the right, in
 * that fixed order. Hand-rolled per-page flex divs were the reason the
 * filter ended up in a different spot (and a different order relative to
 * each page's own action button) on almost every page.
 */
export function PageHeader({
  title,
  description,
  actions,
  filter,
}: {
  title: string;
  description: string;
  actions?: React.ReactNode;
  filter?: React.ReactNode;
}) {
  return (
    // US-68.5: `flex-wrap` alone never protected the title on a phone — an
    // `items-center` actions block marked `shrink-0` left the title as the
    // only side willing to give up width, so it compressed into a
    // one-word-per-line column instead of the row actually wrapping.
    // Stacking below `sm` (title full-width, actions on their own row) and
    // dropping the boilerplate description there reclaims the space a
    // phone doesn't have to spare.
    <div className="flex flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-start sm:justify-between sm:gap-4">
      <div className="min-w-0 sm:flex-1">
        <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">{title}</h1>
        <p className="hidden text-sm text-muted-foreground sm:block">{description}</p>
      </div>
      {(actions || filter) && (
        <div className="flex flex-wrap items-center gap-3 sm:shrink-0 sm:justify-end">
          {actions}
          {filter}
        </div>
      )}
    </div>
  );
}
