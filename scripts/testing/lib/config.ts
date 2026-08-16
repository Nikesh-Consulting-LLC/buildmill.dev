/**
 * Suite configuration, read once from the environment.
 *
 * Everything here is optional except the base URL, which defaults to a local
 * api. A missing credential never fails a test — it *skips* the tests that
 * need it, and the runner prints the skip so nobody mistakes "not exercised"
 * for "passed". That is the whole reason this file exists rather than reading
 * `process.env` at each call site: one place decides what is configured, and
 * one place reports it.
 */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
export const SUITE_ROOT = path.resolve(HERE, "..");

/**
 * A deliberately small .env reader — the suite has exactly one dependency
 * (@playwright/test) and adding dotenv for `KEY=value` would not earn its
 * install. Values already in the real environment always win, so CI can
 * override a developer's file.
 */
function loadDotEnv(): void {
  const file = path.join(SUITE_ROOT, ".env");
  if (!fs.existsSync(file)) return;
  for (const rawLine of fs.readFileSync(file, "utf8").split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#")) continue;
    const eq = line.indexOf("=");
    if (eq === -1) continue;
    const key = line.slice(0, eq).trim();
    let value = line.slice(eq + 1).trim();
    if (
      (value.startsWith('"') && value.endsWith('"')) ||
      (value.startsWith("'") && value.endsWith("'"))
    ) {
      value = value.slice(1, -1);
    }
    if (process.env[key] === undefined) process.env[key] = value;
  }
}

loadDotEnv();

const env = (key: string, fallback = ""): string =>
  (process.env[key] ?? fallback).trim();

const flag = (key: string): boolean =>
  ["1", "true", "yes", "on"].includes(env(key).toLowerCase());

export const config = {
  /** The api under test. Include no trailing slash. */
  apiBaseUrl: env("API_BASE_URL", "http://localhost:8000").replace(/\/+$/, ""),

  /** Supabase project the api verifies JWTs against — used to mint test tokens. */
  supabaseUrl: env("SUPABASE_URL").replace(/\/+$/, ""),
  supabaseAnonKey: env("SUPABASE_ANON_KEY") || env("SUPABASE_PUBLISHABLE_KEY"),

  /** An ordinary member. Either a ready-made token or a password sign-in. */
  userToken: env("TEST_ACCESS_TOKEN"),
  userEmail: env("TEST_USER_EMAIL"),
  userPassword: env("TEST_USER_PASSWORD"),

  /** A platform admin — the only credential that gets past /admin/*. */
  adminToken: env("TEST_ADMIN_ACCESS_TOKEN"),
  adminEmail: env("TEST_ADMIN_EMAIL"),
  adminPassword: env("TEST_ADMIN_PASSWORD"),

  /** A registered worker's registry token, for /worker/* and the git remote. */
  workerToken: env("TEST_WORKER_TOKEN"),

  /** Ids the read-only smoke tests address. Each gates only its own tests. */
  orgId: env("TEST_ORG_ID"),
  projectId: env("TEST_PROJECT_ID"),
  deploymentId: env("TEST_DEPLOYMENT_ID"),
  issueId: env("TEST_ISSUE_ID"),
  releaseId: env("TEST_RELEASE_ID"),
  serverId: env("TEST_SERVER_ID"),
  agentHostId: env("TEST_AGENT_HOST_ID"),
  workerId: env("TEST_WORKER_ID"),

  /**
   * Opt-in for tests that change state. Off by default and it should stay off
   * against anything shared: this api dispatches agent runs, deploys to real
   * servers and deletes orgs. A suite that quietly did that once would not be
   * run a second time.
   */
  allowMutations: flag("ALLOW_MUTATIONS"),

  /** Per-request timeout for the API context, in ms. */
  requestTimeoutMs: Number(env("REQUEST_TIMEOUT_MS", "20000")),
} as const;

/** Where global-setup parks the tokens it minted, for the worker processes. */
export const TOKEN_CACHE = path.join(SUITE_ROOT, ".auth", "tokens.json");

export type ResolvedTokens = {
  user: string;
  admin: string;
  worker: string;
  /** Why a credential is missing, shown in skip messages. */
  notes: Record<string, string>;
};

export function readTokens(): ResolvedTokens {
  const empty: ResolvedTokens = { user: "", admin: "", worker: "", notes: {} };
  if (!fs.existsSync(TOKEN_CACHE)) return empty;
  try {
    return { ...empty, ...JSON.parse(fs.readFileSync(TOKEN_CACHE, "utf8")) };
  } catch {
    return empty;
  }
}
