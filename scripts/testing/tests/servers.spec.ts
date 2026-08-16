/**
 * Registered servers and the SFTP bridge — 18 operations that reach real hosts.
 *
 * The security rule this category is built around: **server credentials are
 * write-only.** SSH passwords and private keys live in the private `data`
 * Storage bucket, readable by the api's service role alone, and no endpoint,
 * response, log or signed URL may echo them back — the UI shows at most
 * "Key set · <fingerprint>".
 *
 * Everything that touches a filesystem is opt-in. The refusal surface is not:
 * a path-traversal attempt and an unknown server id must both be refused
 * cleanly, and those are the tests that matter most here.
 */

import {
  all,
  describeAuthBoundary,
  expect,
  expectDetail,
  needsId,
  needsMutations,
  needsUser,
  NONEXISTENT_UUID,
  test,
  userHeaders,
  whenConfigured,
} from "../lib/suite";
import { config } from "../lib/config";

const SECRET_MARKERS = [
  "-----BEGIN OPENSSH PRIVATE KEY",
  "-----BEGIN RSA PRIVATE KEY",
  "-----BEGIN PRIVATE KEY",
  "PRIVATE KEY-----",
];

test.describe("servers", () => {
  describeAuthBoundary("servers");

  whenConfigured(needsUser(), "signed in", () => {
    test("an unknown server refuses every file operation", async ({ request }) => {
      for (const [method, suffix] of [
        ["GET", "files?path=/"],
        ["GET", "files/read?path=/etc/hostname"],
        ["POST", "files/mkdir"],
        ["POST", "files/delete"],
        ["POST", "files/write"],
        ["POST", "files/new"],
        ["POST", "files/extract"],
      ] as const) {
        const response = await request.fetch(
          `/api/v1/servers/${NONEXISTENT_UUID}/${suffix}`,
          {
            method,
            headers: userHeaders(),
            ...(method === "POST" ? { data: { path: "/tmp/api-test" } } : {}),
            failOnStatusCode: false,
          },
        );
        expect(
          response.status(),
          `${method} ${suffix} answered ${response.status()}`,
        ).toBeGreaterThanOrEqual(400);
        expect(response.status()).toBeLessThan(500);
      }
    });

    test("a connection test against an unreachable host reports, never crashes", async ({
      request,
    }) => {
      // 203.0.113.0/24 is TEST-NET-3: reserved for documentation and
      // guaranteed not to route, so this can never reach a real machine.
      const response = await request.post("/api/v1/servers/test-connection", {
        headers: userHeaders(),
        data: {
          host: "203.0.113.9",
          port: 22,
          username: "api-test",
          password: "api-test",
        },
        failOnStatusCode: false,
      });
      expect(
        response.status(),
        "an unreachable host produced a 500 instead of a reported failure",
      ).toBeLessThan(500);
    });

    test("a connection test never echoes the credential it was given", async ({
      request,
    }) => {
      const password = `api-test-password-${Date.now()}`;
      const response = await request.post("/api/v1/servers/test-connection", {
        headers: userHeaders(),
        data: { host: "203.0.113.9", port: 22, username: "api-test", password },
        failOnStatusCode: false,
      });
      const text = await response.text();
      expect(
        text.includes(password),
        "the response echoed the submitted password",
      ).toBe(false);
    });

    test("a rejected server body does not echo the credential in its 422", async ({
      request,
    }) => {
      // The api strips `input` from every validation error precisely so a
      // pasted key or password cannot ride out in a 422 body.
      const key = "-----BEGIN OPENSSH PRIVATE KEY-----\napi-test\n-----END-----";
      const response = await request.post("/api/v1/servers", {
        headers: userHeaders(),
        data: { private_key: key },
        failOnStatusCode: false,
      });
      const text = await response.text();
      expect(text.includes("api-test\\n-----END")).toBe(false);
      for (const marker of SECRET_MARKERS) {
        expect(
          text.includes(marker),
          `the ${response.status()} body echoed '${marker}'`,
        ).toBe(false);
      }
    });
  });

  whenConfigured(
    all(needsUser(), needsId(config.serverId, "TEST_SERVER_ID")),
    "against a real server",
    () => {
      test("listing files answers without leaking credentials", async ({
        request,
      }) => {
        const response = await request.get(
          `/api/v1/servers/${config.serverId}/files?path=/`,
          { headers: userHeaders(), failOnStatusCode: false },
        );
        expect(response.status()).toBeLessThan(500);
        if (response.status() !== 200) return;
        const text = await response.text();
        for (const marker of SECRET_MARKERS) {
          expect(text.includes(marker), `file listing contains '${marker}'`).toBe(
            false,
          );
        }
      });

      test("a path outside the allowed root is refused", async ({ request }) => {
        const response = await request.get(
          `/api/v1/servers/${config.serverId}/files/read?path=${encodeURIComponent("../../../../etc/shadow")}`,
          { headers: userHeaders(), failOnStatusCode: false },
        );
        expect(response.status()).not.toBe(200);
        expect(response.status()).toBeLessThan(500);
      });
    },
  );

  whenConfigured(
    all(needsUser(), needsId(config.serverId, "TEST_SERVER_ID"), needsMutations()),
    "state-changing (opt-in)",
    () => {
      test("a connection test against the registered server succeeds", async ({
        request,
      }) => {
        const response = await request.post(
          `/api/v1/servers/${config.serverId}/test`,
          { headers: userHeaders(), data: {}, failOnStatusCode: false },
        );
        expect(response.status()).toBeLessThan(500);
      });

      test("a directory can be created and removed", async ({ request }) => {
        const dir = `/tmp/api-test-${Date.now()}`;
        const made = await request.post(
          `/api/v1/servers/${config.serverId}/files/mkdir`,
          { headers: userHeaders(), data: { path: dir }, failOnStatusCode: false },
        );
        expect(made.status()).toBeLessThan(500);
        if (made.status() >= 400) {
          await expectDetail(made);
          return;
        }
        const removed = await request.post(
          `/api/v1/servers/${config.serverId}/files/delete`,
          { headers: userHeaders(), data: { path: dir }, failOnStatusCode: false },
        );
        expect(removed.status()).toBeLessThan(400);
      });
    },
  );
});
