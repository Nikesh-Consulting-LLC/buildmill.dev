/**
 * The factory git remote — smart-HTTP proxy at `/git/*`.
 *
 * Git-native workers clone and push through here using the worker token as the
 * HTTP Basic password, so they never hold GitHub credentials. That makes two
 * behaviours load-bearing: the 401 must carry a `WWW-Authenticate: Basic`
 * challenge (git clients retry only when they get one — without it a clone
 * fails outright instead of prompting), and the proxy must never relay
 * GitHub's own credential errors to a worker who cannot act on them.
 */

import {
  describeAuthBoundary,
  expect,
  needsWorker,
  test,
  tokens,
  whenConfigured,
} from "../lib/suite";

const REPO = "/git/api-test-org/api-test-project";

test.describe("git", () => {
  describeAuthBoundary("git");

  test("info/refs answers 401 with a Basic challenge", async ({ request }) => {
    const response = await request.get(
      `${REPO}/info/refs?service=git-upload-pack`,
      { failOnStatusCode: false },
    );
    expect(response.status()).toBe(401);
    expect(
      response.headers()["www-authenticate"] ?? "",
      "a git client only retries with credentials when challenged",
    ).toContain("Basic");
  });

  test("a malformed Authorization header is a 401, not a crash", async ({
    request,
  }) => {
    for (const value of ["Basic", "Basic !!!!", "Basic " + "=".repeat(10)]) {
      const response = await request.get(
        `${REPO}/info/refs?service=git-upload-pack`,
        { headers: { Authorization: value }, failOnStatusCode: false },
      );
      expect(response.status(), `'${value}' answered ${response.status()}`).toBe(
        401,
      );
    }
  });

  test("a forged worker token is refused on upload-pack and receive-pack", async ({
    request,
  }) => {
    const credential = Buffer.from("worker:forged-token").toString("base64");
    for (const service of ["git-upload-pack", "git-receive-pack"]) {
      const response = await request.post(`${REPO}/${service}`, {
        headers: {
          Authorization: `Basic ${credential}`,
          "Content-Type": `application/x-${service}-request`,
        },
        data: "0000",
        failOnStatusCode: false,
      });
      expect(
        response.status(),
        `${service} answered ${response.status()} to a forged token`,
      ).toBe(401);
    }
  });

  whenConfigured(needsWorker(), "as a registered worker", () => {
    test("a real token reaches the repository resolution step", async ({
      request,
    }) => {
      // The org/project in the URL is fictional, so a 403/404 is the expected
      // answer — what is being asserted is that authentication passed and the
      // proxy moved on to authorization rather than stopping at 401.
      const credential = Buffer.from(`worker:${tokens.worker}`).toString("base64");
      const response = await request.get(
        `${REPO}/info/refs?service=git-upload-pack`,
        {
          headers: { Authorization: `Basic ${credential}` },
          failOnStatusCode: false,
        },
      );
      expect(
        response.status(),
        "a valid worker token was still refused as unauthenticated",
      ).not.toBe(401);
      expect([403, 404]).toContain(response.status());
    });

    test("an unauthorized repository is refused with a reason, not GitHub's 401", async ({
      request,
    }) => {
      const credential = Buffer.from(`worker:${tokens.worker}`).toString("base64");
      const response = await request.get(
        `${REPO}/info/refs?service=git-upload-pack`,
        {
          headers: { Authorization: `Basic ${credential}` },
          failOnStatusCode: false,
        },
      );
      const text = await response.text();
      expect(text.toLowerCase()).not.toContain("username or password");
      expect(text).not.toContain(tokens.worker);
    });
  });
});
