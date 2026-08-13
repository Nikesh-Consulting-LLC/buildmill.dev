// POSIX path helpers for the remote file manager (server paths are always
// POSIX, regardless of the browser's OS).

export function joinPath(dir: string, name: string): string {
  if (name.startsWith("/")) return name;
  const base = dir.replace(/\/+$/, "");
  return base === "" ? `/${name}` : `${base}/${name}`;
}

export function parentPath(dir: string): string {
  const trimmed = dir.replace(/\/+$/, "");
  const idx = trimmed.lastIndexOf("/");
  if (idx <= 0) return "/";
  return trimmed.slice(0, idx);
}

export function baseName(path: string): string {
  const trimmed = path.replace(/\/+$/, "");
  const idx = trimmed.lastIndexOf("/");
  return idx === -1 ? trimmed : trimmed.slice(idx + 1);
}

export type Crumb = { label: string; path: string };

export function breadcrumbs(path: string): Crumb[] {
  const parts = path.split("/").filter(Boolean);
  const crumbs: Crumb[] = [{ label: "/", path: "/" }];
  let acc = "";
  for (const part of parts) {
    acc += `/${part}`;
    crumbs.push({ label: part, path: acc });
  }
  return crumbs;
}
