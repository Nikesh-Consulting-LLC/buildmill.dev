/** US-118.1: a template's face — the pure rules behind the cover.
 *
 * `image_path` on `project_templates` / `org_project_templates` has three
 * shapes (migration 284), and this module is the one place the web turns
 * them into pixels:
 *
 *   null                     → a generated cover: the name's initials on a
 *                              tint picked by a stable hash of the name
 *   builtin/<name>           → `/template-covers/<name>.svg`, shipped with
 *                              the app (apps/web/public/template-covers/)
 *   catalog/<id>/cover
 *   <org>/<id>/cover         → an object in the public `template-images`
 *                              bucket, cache-busted by the row's updated_at
 *
 * Nothing here touches the network or the DOM, so it is testable under
 * `node --test`; the components in `template-card.tsx` compose it. */

export const TEMPLATE_IMAGE_BUCKET = "template-images";
export const TEMPLATE_IMAGE_MAX_BYTES = 2 * 1024 * 1024;
export const TEMPLATE_IMAGE_TYPES: readonly string[] = [
  "image/png",
  "image/jpeg",
  "image/webp",
  "image/gif",
  "image/svg+xml",
];
export const TEMPLATE_IMAGE_TYPES_LABEL = "PNG, JPEG, WebP, GIF or SVG";

/** The covers shipped with the web app. `name` is what `image_path` stores
 * after `builtin/`; the file is `public/template-covers/<name>.svg`. Order
 * is display order in the details dialog. */
export const BUILTIN_COVERS = [
  { name: "factory", label: "Factory default" },
  { name: "web-app", label: "Web app" },
  { name: "full-stack", label: "Full stack" },
  { name: "service", label: "Service" },
  { name: "site", label: "Site" },
  { name: "mobile-app", label: "Mobile app" },
  // us-118.6: the CRE demo templates' faces — generic enough to reuse.
  { name: "data-pipeline", label: "Data pipeline" },
  { name: "bi-model", label: "BI model" },
  { name: "spreadsheet", label: "Spreadsheet" },
  { name: "finance-model", label: "Finance model" },
  { name: "assistant", label: "Assistant" },
  // us-118.5: the kinds of project the factory actually builds.
  { name: "python-react", label: "Python + React app" },
  { name: "database", label: "Database project" },
  { name: "report-conversion", label: "Report conversion" },
  { name: "power-bi", label: "Power BI dashboards" },
  { name: "corporate-website", label: "Corporate website" },
  { name: "conversational-agents", label: "Conversational agents" },
  { name: "sharepoint", label: "SharePoint project" },
  { name: "salesforce", label: "Salesforce integration" },
  { name: "yardi-reports", label: "Yardi reports" },
] as const;

export type BuiltinCoverName = (typeof BUILTIN_COVERS)[number]["name"];

const BUILTIN_RE = /^builtin\/([a-z0-9-]{1,40})$/;

export function isBuiltinCover(path: string | null | undefined): boolean {
  return !!path && BUILTIN_RE.test(path);
}

export function builtinCoverPath(name: BuiltinCoverName | string): string {
  return `builtin/${name}`;
}

/** The public URL of a stored cover, or null when there is none.
 *
 * `supabaseUrl` is the project URL (`NEXT_PUBLIC_SUPABASE_URL`); the shape
 * matches what `storage.from(bucket).getPublicUrl(path)` returns, without
 * needing a client. `updatedAt` cache-busts: the object path is fixed and
 * replaced by upsert, so the same URL would otherwise show the old image
 * until a hard reload. */
export function templateCoverUrl(
  imagePath: string | null | undefined,
  updatedAt: string | null | undefined,
  supabaseUrl: string | undefined,
): string | null {
  if (!imagePath) return null;
  const builtin = BUILTIN_RE.exec(imagePath);
  if (builtin) return `/template-covers/${builtin[1]}.svg`;
  if (!supabaseUrl) return null;
  const base = supabaseUrl.replace(/\/+$/, "");
  const url = `${base}/storage/v1/object/public/${TEMPLATE_IMAGE_BUCKET}/${imagePath}`;
  if (!updatedAt) return url;
  const stamp = Date.parse(updatedAt);
  return `${url}?v=${Number.isNaN(stamp) ? encodeURIComponent(updatedAt) : stamp}`;
}

/** Up to two initials: the first letter of the first two words, upper-case.
 * "Python + Next.JS Web App" → "PN", "Generic Web App" → "GW", "Default" →
 * "D". Punctuation is not a word; an empty name gives "?". */
export function templateInitials(name: string): string {
  const words = name
    .replace(/[^\p{L}\p{N}]+/gu, " ")
    .trim()
    .split(/\s+/)
    .filter(Boolean);
  if (words.length === 0) return "?";
  const first = words[0][0];
  const second = words.length > 1 ? words[1][0] : "";
  return (first + second).toUpperCase();
}

export type TemplateTint = "a" | "b" | "c";
const TINTS: readonly TemplateTint[] = ["a", "b", "c"];

/** One of three tints, chosen by a stable hash of the name — the same name
 * always gets the same tint, on every render and in both themes. */
export function templateTint(name: string): TemplateTint {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0;
  return TINTS[h % TINTS.length];
}

/** Client-side check before any upload: the message names the limit, and
 * returns null when the file is acceptable. */
export function templateImageProblem(file: {
  type: string;
  size: number;
  name: string;
}): string | null {
  if (!TEMPLATE_IMAGE_TYPES.includes(file.type)) {
    return `"${file.name}" is not a supported image (${TEMPLATE_IMAGE_TYPES_LABEL}).`;
  }
  if (file.size > TEMPLATE_IMAGE_MAX_BYTES) {
    return `"${file.name}" is over the 2 MB limit.`;
  }
  return null;
}

/** The object path the browser uploads a catalog template's cover to. */
export function catalogCoverObject(templateId: string): string {
  return `catalog/${templateId}/cover`;
}

/** The object path the browser uploads an org template's own cover to. */
export function orgCoverObject(orgId: string, orgTemplateId: string): string {
  return `${orgId}/${orgTemplateId}/cover`;
}

/** Whether `imagePath` is an object this org owns (and may delete). A copy
 * pointing at the catalog's object, or a built-in, is not. */
export function isOwnOrgCover(imagePath: string | null | undefined, orgId: string): boolean {
  return !!imagePath && imagePath.startsWith(`${orgId}/`);
}
