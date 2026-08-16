/**
 * Sign in once per run, not once per worker process.
 *
 * Playwright workers are separate processes, so a token minted inside a spec
 * would be minted again by every worker — six sign-ins against Supabase for one
 * suite. This runs before any of them, exchanges the configured passwords for
 * access tokens, and writes them where `readTokens()` picks them up.
 *
 * A sign-in that fails is recorded as a note, never thrown: the suite must
 * still run its unauthenticated half against an api nobody has credentials for.
 */

import fs from "node:fs";
import path from "node:path";

import { config, TOKEN_CACHE, type ResolvedTokens } from "./config";

async function signIn(
  email: string,
  password: string,
): Promise<{ token: string; error: string }> {
  if (!config.supabaseUrl || !config.supabaseAnonKey) {
    return {
      token: "",
      error: "SUPABASE_URL / SUPABASE_ANON_KEY are not set",
    };
  }
  try {
    const response = await fetch(
      `${config.supabaseUrl}/auth/v1/token?grant_type=password`,
      {
        method: "POST",
        headers: {
          apikey: config.supabaseAnonKey,
          Authorization: `Bearer ${config.supabaseAnonKey}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ email, password }),
      },
    );
    const payload = (await response.json().catch(() => ({}))) as Record<
      string,
      unknown
    >;
    if (!response.ok) {
      const detail =
        (payload.error_description as string) ??
        (payload.msg as string) ??
        `HTTP ${response.status}`;
      return { token: "", error: `sign-in refused: ${detail}` };
    }
    const token = String(payload.access_token ?? "");
    return token
      ? { token, error: "" }
      : { token: "", error: "sign-in returned no access_token" };
  } catch (cause) {
    return { token: "", error: `sign-in failed: ${(cause as Error).message}` };
  }
}

async function resolve(
  label: string,
  presetToken: string,
  email: string,
  password: string,
  notes: Record<string, string>,
): Promise<string> {
  if (presetToken) return presetToken;
  if (!email || !password) {
    notes[label] = `no ${label} credentials configured`;
    return "";
  }
  const { token, error } = await signIn(email, password);
  if (!token) notes[label] = `${label}: ${error}`;
  return token;
}

export default async function globalSetup(): Promise<void> {
  const notes: Record<string, string> = {};

  const tokens: ResolvedTokens = {
    user: await resolve(
      "user",
      config.userToken,
      config.userEmail,
      config.userPassword,
      notes,
    ),
    admin: await resolve(
      "admin",
      config.adminToken,
      config.adminEmail,
      config.adminPassword,
      notes,
    ),
    worker: config.workerToken,
    notes,
  };
  if (!tokens.worker) notes.worker = "TEST_WORKER_TOKEN is not set";

  fs.mkdirSync(path.dirname(TOKEN_CACHE), { recursive: true });
  fs.writeFileSync(TOKEN_CACHE, JSON.stringify(tokens, null, 2), "utf8");

  const have = (["user", "admin", "worker"] as const)
    .map((k) => `${k}=${tokens[k] ? "yes" : "no"}`)
    .join(" ");
  console.log(`[api-tests] target ${config.apiBaseUrl} · credentials ${have}`);
  for (const note of Object.values(notes)) console.log(`[api-tests] ${note}`);
  if (config.allowMutations) {
    console.log("[api-tests] ALLOW_MUTATIONS is on — state-changing tests will run");
  }
}
