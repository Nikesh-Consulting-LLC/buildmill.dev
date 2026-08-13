// US-73.1: the credentialed clone URL the Connect tab displays. gitproxy
// authenticates on the token alone, so the username is cosmetic — but it must
// survive URL parsing, which is why an email's `@` becomes `.` (git splits
// credentials at the first raw `@`, and `%40` reads badly).

import { strict as assert } from "node:assert";
import { test } from "node:test";
import {
  emailAsGitUsername,
  factoryRemoteUrl,
  factoryRemoteUrlWithCreds,
} from "./factory-git.ts";

const HOST = factoryRemoteUrl("acme", "app"); // e.g. http://localhost:8000/git/acme/app.git

test("embeds worker credentials into the remote URL", () => {
  const url = factoryRemoteUrlWithCreds("acme", "app", "worker", "sfw_abc123");
  assert.equal(url, HOST.replace("://", "://worker:sfw_abc123@"));
});

test("an email username maps @ to . and needs no encoding", () => {
  const username = emailAsGitUsername("user@example.com");
  assert.equal(username, "user.example.com");
  const url = factoryRemoteUrlWithCreds("acme", "app", username, "sfw_abc123");
  assert.equal(url, HOST.replace("://", "://user.example.com:sfw_abc123@"));
  // Round-trips: the URL parser reads the same credentials back out, raw.
  const parsed = new URL(url);
  assert.equal(parsed.username, "user.example.com");
  assert.equal(parsed.password, "sfw_abc123");
});
