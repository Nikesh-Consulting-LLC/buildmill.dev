import assert from "node:assert/strict";
import { test } from "node:test";

import { classifyError } from "./error-classify.ts";

test("WebKit's Load failed is a network error (prod BUG-5)", () => {
  assert.equal(
    classifyError({ name: "TypeError", message: "Load failed" }),
    "network",
  );
});

test("Chromium's Failed to fetch is a network error", () => {
  assert.equal(
    classifyError({ name: "TypeError", message: "Failed to fetch" }),
    "network",
  );
});

test("Firefox's NetworkError is a network error", () => {
  assert.equal(
    classifyError({
      name: "TypeError",
      message: "NetworkError when attempting to fetch resource.",
    }),
    "network",
  );
});

test("a stale chunk after a deploy is a network error", () => {
  assert.equal(
    classifyError({
      name: "TypeError",
      message: "error loading dynamically imported module: https://x/chunk.js",
    }),
    "network",
  );
  assert.equal(
    classifyError({ name: "ChunkLoadError", message: "Loading chunk 42 failed" }),
    "network",
  );
});

test("our own NetworkError classifies by its message", () => {
  assert.equal(
    classifyError({
      name: "NetworkError",
      message: "could not reach the API (POST /api/v1/runs/x/approve)",
    }),
    "network",
  );
});

test("an app defect is not a network error", () => {
  assert.equal(
    classifyError({
      name: "TypeError",
      message: "Cannot read properties of undefined (reading 'map')",
    }),
    null,
  );
  assert.equal(classifyError({}), null);
});
