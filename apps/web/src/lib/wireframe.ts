/** US-48.2: reading a wireframe artifact on the server side.
 *
 * The artifact stores the DECLARATION the kit renders — screens, regions,
 * components, states — not the rendered HTML. So summarising a wireframe for
 * the work item page is reading JSON, never parsing a document, and the same
 * shape is what the API hands a plan run in US-48.4.
 *
 * Everything here tolerates a malformed declaration rather than throwing: the
 * content is agent-authored, and a story page must not 500 because one
 * hand-back was odd. */

import type { WireframeState } from "@/app/(app)/issues/[id]/wireframe-panel";

export type WireframeDeclaration = {
  screens?: unknown;
  no_ui_surface?: boolean;
  reason?: string;
};

export function parseDeclaration(content: unknown): WireframeDeclaration {
  if (content && typeof content === "object") {
    return content as WireframeDeclaration;
  }
  if (typeof content !== "string") return {};
  try {
    const parsed = JSON.parse(content);
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

export function declaredScreens(
  declaration: WireframeDeclaration
): { name: string; route: string | null }[] {
  const screens = declaration.screens;
  if (!Array.isArray(screens)) return [];
  return screens.flatMap((screen) => {
    if (!screen || typeof screen !== "object") return [];
    const s = screen as { name?: unknown; route?: unknown };
    return [
      {
        name: typeof s.name === "string" ? s.name : "Screen",
        route: typeof s.route === "string" ? s.route : null,
      },
    ];
  });
}

/** The one-line summary the panel shows. Mirrors `wireframes.summarize` in
 * the API — the two are read side by side (the panel here, the hand-back
 * message there), so they say the same thing in the same words. */
export function summarize(declaration: WireframeDeclaration): string | null {
  const screens = declaredScreens(declaration);
  if (!screens.length) return null;
  return `${screens.length} screen${screens.length === 1 ? "" : "s"}`;
}

export function buildWireframeState({
  artifact,
  inFlight,
  displayId,
  repoFullName,
  defaultBranch,
}: {
  artifact: { content: unknown; version: number | null } | null;
  inFlight: boolean;
  displayId: string | null;
  repoFullName: string | null;
  defaultBranch: string | null;
}): WireframeState {
  if (!artifact) {
    return {
      version: null,
      noUiSurface: false,
      reason: null,
      summary: null,
      screens: [],
      inFlight,
      repoPath: null,
      repoUrl: null,
    };
  }
  const declaration = parseDeclaration(artifact.content);
  const noUiSurface = declaration.no_ui_surface === true;
  const path =
    displayId && !noUiSurface
      ? `docs/wireframes/${displayId.toLowerCase()}.html`
      : null;
  return {
    version: artifact.version,
    noUiSurface,
    reason: typeof declaration.reason === "string" ? declaration.reason : null,
    summary: summarize(declaration),
    screens: declaredScreens(declaration),
    inFlight,
    repoPath: path,
    repoUrl:
      path && repoFullName
        ? `https://github.com/${repoFullName}/blob/${defaultBranch || "main"}/${path}`
        : null,
  };
}
