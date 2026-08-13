// US-71.1: the create dialog's epic-picker model, extracted pure so the
// ordering/filter/default rules are testable. Deliberately imports nothing —
// `npm run test:web` loads it with node --test, which resolves neither `@/`
// aliases nor extensionless ESM specifiers.

export type PickerEpic = {
  id: string;
  title: string;
  number?: number | null;
  active?: boolean;
  status?: string;
};

/** Open epics newest-number-first, plus — only when editing — the closed
 * epic the item already sits on, kept at the end so the select cannot fall
 * back to another value and silently move the item on save (US-20.3). */
export function epicPickerOptions<T extends PickerEpic>(
  epics: T[],
  currentEpicId?: string | null
): T[] {
  const open = epics
    .filter((e) => e.status !== "completed")
    .sort((a, b) => (b.number ?? -1) - (a.number ?? -1));
  const current = epics.filter(
    (e) => e.status === "completed" && e.id === currentEpicId
  );
  return [...open, ...current];
}

/** The epic a brand-new item defaults into: the active epic (US-14.4), or —
 * now that closing can leave a project with no active epic — the newest open
 * one. Null when every epic is closed (the dialog falls back to "none"). */
export function defaultEpicId(epics: PickerEpic[]): string | null {
  return (
    epics.find((e) => e.active && e.status !== "completed")?.id ??
    epicPickerOptions(epics)[0]?.id ??
    null
  );
}
