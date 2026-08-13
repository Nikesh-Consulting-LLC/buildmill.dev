// US-3.9/US-3.13: the factory git remote and MCP URLs the UI displays.
// Safe in server and client components — reads only NEXT_PUBLIC_ env.

export const FACTORY_API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/** The project's clone/push address through the factory (us-3.8/us-3.13). */
export function factoryRemoteUrl(orgShortname: string, projectSlug: string) {
  return `${FACTORY_API_URL}/git/${orgShortname}/${projectSlug}.git`;
}

/** A member's email as a git username (us-73.1) — `@` becomes `.` so the URL
 * needs no percent-encoding (git splits credentials at the first raw `@`,
 * and `%40` reads badly). Dot chosen for readability; in principle two
 * emails can collide (`a.b@c.com` / `a@b.c.com` → `a.b.c.com`) — accepted,
 * since gitproxy ignores the username and this is a display label only. */
export function emailAsGitUsername(email: string) {
  return email.replace("@", ".");
}

/** The clone URL with credentials embedded (us-73.1) — gitproxy authenticates
 * on the token alone and ignores the username, so any username is valid; a
 * human's email doubles as a readable one. */
export function factoryRemoteUrlWithCreds(
  orgShortname: string,
  projectSlug: string,
  username: string,
  token: string,
) {
  const url = new URL(factoryRemoteUrl(orgShortname, projectSlug));
  url.username = username;
  url.password = token;
  return url.toString();
}

/** The single MCP server URL — every worker token hits the same endpoint;
 * scope now comes from the token itself (workers.project_id), not the URL. */
export function factoryMcpUrl() {
  return `${FACTORY_API_URL}/mcp`;
}

export function githubRepoUrl(repoFullName: string) {
  return `https://github.com/${repoFullName}`;
}
