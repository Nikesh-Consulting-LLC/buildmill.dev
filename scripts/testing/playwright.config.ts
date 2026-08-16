import { defineConfig } from "@playwright/test";

import { config } from "./lib/config";

/**
 * An API-only Playwright project: every spec uses the `request` fixture, so no
 * browser binary is ever launched and `npx playwright install` is unnecessary.
 *
 * `retries: 0` on purpose — these tests assert status codes against a live
 * service, and a retry would paper over exactly the flakiness worth seeing.
 */
export default defineConfig({
  testDir: "./tests",
  globalSetup: "./lib/global-setup.ts",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: 0,
  workers: Number(process.env.PW_WORKERS ?? 6),
  timeout: config.requestTimeoutMs + 10_000,
  expect: { timeout: 5_000 },
  reporter: process.env.PW_JSON_OUTPUT
    ? [["json", { outputFile: process.env.PW_JSON_OUTPUT }], ["line"]]
    : [["list"]],
  use: {
    baseURL: config.apiBaseUrl,
    extraHTTPHeaders: {
      Accept: "application/json",
      // Marks this traffic in the api's request log (US-62.8) so a run is
      // distinguishable from real usage when reading the performance tables.
      "User-Agent": "buildmill-api-tests/1.0 (playwright)",
    },
    ignoreHTTPSErrors: true,
  },
});
