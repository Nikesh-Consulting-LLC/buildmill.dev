import { readFileSync } from "node:fs";
import { join } from "node:path";
import type { NextConfig } from "next";

// US-51.1: apps/web/VERSION is stamped by the deploy workflows (git describe)
// before rsync — the deployed tree has no .git, so the build can't ask git.
// Absent on local checkouts; app-version.ts then reads "dev".
function buildVersion(): string {
  try {
    return readFileSync(join(process.cwd(), "VERSION"), "utf8").trim();
  } catch {
    return "";
  }
}

const nextConfig: NextConfig = {
  env: {
    NEXT_PUBLIC_APP_VERSION: buildVersion(),
  },
  experimental: {
    // US-87.11: React's <ViewTransition> over the browser's View Transitions
    // API, so a route change and a skeleton→content handoff animate instead
    // of popping. Browsers without support simply do not animate — nothing
    // breaks, and no JS animation library is involved.
    viewTransition: true,
  },
};

export default nextConfig;
