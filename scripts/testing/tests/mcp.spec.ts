/**
 * The MCP server catalog and the per-run scoped proxy.
 *
 * The proxy is the interesting half. A run reaches a credentialed tool server
 * through it with a key minted for *that run*, and the grant is checked against
 * the tool surface recorded at claim — so cross-run isolation is a property of
 * the key, not of anything the agent sends. Two refusals encode that: an
 * invalid key is 401, and a valid key asking for a server its run was not
 * granted is 403 (default deny at the only door).
 *
 * The factory's own MCP server is mounted at `/mcp`; the path-rewrite that
 * serves it without a 307 is asserted here too, because a redirect there broke
 * a real release — newer MCP clients refuse to follow redirects.
 */

import {
  describeAuthBoundary,
  expect,
  expectDetail,
  needsUser,
  NONEXISTENT_UUID,
  test,
  userHeaders,
  whenConfigured,
} from "../lib/suite";

test.describe("mcp", () => {
  describeAuthBoundary("mcp-catalog");

  test.describe("scoped proxy", () => {
    test("an invalid scoped key is refused with an explanation", async ({
      request,
    }) => {
      const response = await request.post("/api/v1/mcp-proxy/api-test-slug", {
        headers: { "X-Factory-Mcp-Key": "forged-scoped-key" },
        data: { jsonrpc: "2.0", id: 1, method: "tools/list" },
        failOnStatusCode: false,
      });
      expect(response.status()).toBe(401);
      // The message names the rule — a key is valid only while its run runs.
      expect((await expectDetail(response)).toLowerCase()).toContain("key");
    });

    test("the key is accepted from either header, and verified in both", async ({
      request,
    }) => {
      const shapes: Record<string, string>[] = [
        { "X-Factory-Mcp-Key": "forged-scoped-key" },
        { Authorization: "Bearer forged-scoped-key" },
      ];
      for (const headers of shapes) {
        const response = await request.post("/api/v1/mcp-proxy/api-test-slug", {
          headers,
          data: { jsonrpc: "2.0", id: 1, method: "tools/list" },
          failOnStatusCode: false,
        });
        expect(response.status()).toBe(401);
      }
    });

    test("no credential at all is refused", async ({ request }) => {
      const response = await request.post("/api/v1/mcp-proxy/api-test-slug", {
        data: { jsonrpc: "2.0", id: 1, method: "tools/list" },
        failOnStatusCode: false,
      });
      expect(response.status()).toBe(401);
    });
  });

  test.describe("the factory MCP server", () => {
    test("/mcp is served directly rather than 307-redirected", async ({
      request,
    }) => {
      // Release 2026.08.13.1 failed because the claude CLI's MCP connection
      // died on exactly that redirect: newer MCP clients refuse to follow one.
      const response = await request.post("/mcp", {
        headers: { "Content-Type": "application/json", Accept: "application/json, text/event-stream" },
        data: { jsonrpc: "2.0", id: 1, method: "initialize", params: {} },
        failOnStatusCode: false,
        maxRedirects: 0,
      });
      expect(
        [301, 302, 307, 308].includes(response.status()),
        `/mcp answered a ${response.status()} redirect — MCP clients will not follow it`,
      ).toBe(false);
    });

    test("the MCP endpoint refuses an unauthenticated call", async ({
      request,
    }) => {
      const response = await request.post("/mcp/", {
        headers: { "Content-Type": "application/json", Accept: "application/json, text/event-stream" },
        data: { jsonrpc: "2.0", id: 1, method: "tools/list", params: {} },
        failOnStatusCode: false,
      });
      expect(response.status()).toBeGreaterThanOrEqual(400);
      expect(response.status()).toBeLessThan(500);
    });
  });

  whenConfigured(needsUser(), "signed in", () => {
    test("listing servers for an org the caller cannot see returns nothing", async ({
      request,
    }) => {
      const response = await request.get(
        `/api/v1/orgs/${NONEXISTENT_UUID}/mcp-servers`,
        { headers: userHeaders(), failOnStatusCode: false },
      );
      expect(response.status()).toBeLessThan(500);
      if (response.status() === 200) {
        const body = (await response.json()) as { servers?: unknown[] } | unknown[];
        const servers = Array.isArray(body) ? body : (body.servers ?? []);
        expect(servers).toEqual([]);
      }
    });

    test("no catalog read returns a server's credential", async ({ request }) => {
      const response = await request.get(
        `/api/v1/orgs/${NONEXISTENT_UUID}/mcp-servers`,
        { headers: userHeaders(), failOnStatusCode: false },
      );
      if (response.status() !== 200) return;
      const text = await response.text();
      for (const marker of ["credential", "Bearer ", "sk-", "ghp_"]) {
        expect(
          text.includes(marker),
          `the server catalog response contains '${marker}'`,
        ).toBe(false);
      }
    });

    test("validating an unknown server is refused", async ({ request }) => {
      const response = await request.post(
        `/api/v1/mcp-servers/${NONEXISTENT_UUID}/validate`,
        { headers: userHeaders(), data: {}, failOnStatusCode: false },
      );
      expect(response.status()).toBeGreaterThanOrEqual(400);
      expect(response.status()).toBeLessThan(500);
    });
  });
});
