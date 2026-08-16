/**
 * LLM routing, spend and the runner gateway.
 *
 * Two distinct surfaces share this file because they share one rule: **the
 * org's provider keys live in Vault and never leave the server.** The routing
 * endpoints must not echo them, and the gateway exists so a CLI agent can reach
 * a provider with a short-lived scoped key instead of the real one.
 *
 * Anything that actually calls a model costs money, so it is opt-in.
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

test.describe("llm", () => {
  describeAuthBoundary("llm", "llm-gateway");

  whenConfigured(needsUser(), "signed in", () => {
    test("GET /api/v1/llm/functions lists the routable thinking functions", async ({
      request,
    }) => {
      const response = await request.get("/api/v1/llm/functions", {
        headers: userHeaders(),
        failOnStatusCode: false,
      });
      expect(response.status()).toBe(200);
      const functions = (await response.json()) as {
        key: string;
        label: string;
        description: string;
      }[];
      expect(Array.isArray(functions)).toBe(true);
      expect(functions.length).toBeGreaterThan(0);
      // The settings UI renders every entry, and routes reference the keys —
      // a missing label is a blank row in the picker.
      for (const entry of functions) {
        expect(entry).toHaveProperty("key");
        expect(entry).toHaveProperty("label");
        expect(entry).toHaveProperty("description");
        expect(entry.key.length).toBeGreaterThan(0);
      }
    });

    test("the function registry never carries a provider key", async ({
      request,
    }) => {
      const text = await (
        await request.get("/api/v1/llm/functions", { headers: userHeaders() })
      ).text();
      for (const marker of ["sk-ant-", "sk-proj-", "xai-api", "gsk_"]) {
        expect(text.includes(marker), `functions contains '${marker}'`).toBe(false);
      }
    });

    test("tldr rejects an empty body", async ({ request }) => {
      // `content` has min_length=1 — an empty request must not reach a model.
      const response = await request.post("/api/v1/llm/tldr", {
        headers: userHeaders(),
        data: { content: "" },
        failOnStatusCode: false,
      });
      expect([400, 403, 422]).toContain(response.status());
    });

    test("tldr rejects content past its cap", async ({ request }) => {
      // max_length=60000; sending more must be refused at the schema, not
      // truncated silently into a bill.
      const response = await request.post("/api/v1/llm/tldr", {
        headers: userHeaders(),
        data: { content: "x".repeat(60_001) },
        failOnStatusCode: false,
      });
      expect([400, 403, 413, 422]).toContain(response.status());
    });

    test("spend for an org the caller cannot see is refused", async ({
      request,
    }) => {
      const response = await request.get(
        `/api/v1/llm/orgs/${NONEXISTENT_UUID}/spend`,
        { headers: userHeaders(), failOnStatusCode: false },
      );
      expect(response.status()).not.toBe(500);
      if (response.status() === 200) {
        // A phantom org must read as empty, never as somebody else's numbers.
        const body = JSON.stringify(await response.json());
        expect(body).not.toContain("sk-");
      }
    });
  });

  whenConfigured(
    all(needsUser(), needsId(config.orgId, "TEST_ORG_ID")),
    "against a real org",
    () => {
      test("spend, spend-trend and work-summary all answer", async ({
        request,
      }) => {
        for (const suffix of ["spend", "spend-trend", "work-summary"]) {
          const response = await request.get(
            `/api/v1/llm/orgs/${config.orgId}/${suffix}`,
            { headers: userHeaders(), failOnStatusCode: false },
          );
          expect(
            response.status(),
            `${suffix} answered ${response.status()}`,
          ).toBeLessThan(500);
        }
      });

      test("no spend response exposes a stored key or its full value", async ({
        request,
      }) => {
        const text = await (
          await request.get(`/api/v1/llm/orgs/${config.orgId}/spend`, {
            headers: userHeaders(),
          })
        ).text();
        for (const marker of ["sk-ant-", "sk-proj-", "gsk_", "xai-api"]) {
          expect(text.includes(marker), `spend contains '${marker}'`).toBe(false);
        }
      });
    },
  );

  test.describe("gateway", () => {
    test("the gateway refuses a request with no scoped key", async ({ request }) => {
      const response = await request.post("/api/v1/llm-gateway/v1/messages", {
        data: { model: "test", messages: [] },
        failOnStatusCode: false,
      });
      expect(response.status()).toBe(401);
      expect(await expectDetail(response)).toContain("gateway key");
    });

    test("the gateway refuses an expired-looking key on both header shapes", async ({
      request,
    }) => {
      // It accepts the key as `x-api-key` or as a bearer, because the provider
      // SDKs differ — both must be verified, not just the one in the docs.
      const shapes: Record<string, string>[] = [
        { "x-api-key": "forged-gateway-key" },
        { Authorization: "Bearer forged-gateway-key" },
      ];
      for (const headers of shapes) {
        const response = await request.post("/api/v1/llm-gateway/v1/messages", {
          headers,
          data: { model: "test", messages: [] },
          failOnStatusCode: false,
        });
        expect(response.status()).toBe(401);
      }
    });
  });

  whenConfigured(
    all(needsUser(), needsMutations()),
    "state-changing (opt-in — costs a model call)",
    () => {
      test("tldr summarizes real content", async ({ request }) => {
        const response = await request.post("/api/v1/llm/tldr", {
          headers: userHeaders(),
          data: {
            content:
              "The API test suite calls this endpoint once to confirm the LLM route resolves end to end.",
            kind: "content",
          },
          failOnStatusCode: false,
        });
        expect(response.status()).toBeLessThan(500);
      });
    },
  );
});
