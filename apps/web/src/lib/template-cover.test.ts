import { test } from "node:test";
import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import {
  BUILTIN_COVERS,
  builtinCoverPath,
  catalogCoverObject,
  isBuiltinCover,
  isOwnOrgCover,
  orgCoverObject,
  templateCoverUrl,
  templateImageProblem,
  templateInitials,
  templateTint,
} from "./template-cover.ts";

// US-118.1 AC8/AC11: the generated cover's initials, pinned on the three
// names the catalog holds today plus the edges.

test("initials: first letters of the first two words, upper-case", () => {
  assert.equal(templateInitials("Python + Next.JS Web App"), "PN");
  assert.equal(templateInitials("Generic Web App"), "GW");
  assert.equal(templateInitials("Default"), "D");
  assert.equal(templateInitials("fastapi service"), "FS");
});

test("initials: punctuation is not a word, and an empty name still draws", () => {
  assert.equal(templateInitials("  ++  web  --  app "), "WA");
  assert.equal(templateInitials("---"), "?");
  assert.equal(templateInitials(""), "?");
});

test("tint is stable for the same name and one of three", () => {
  const seen = new Set<string>();
  for (const name of ["Default", "Generic Web App", "Python + Next.JS Web App", "x", ""]) {
    const t1 = templateTint(name);
    const t2 = templateTint(name);
    assert.equal(t1, t2);
    assert.ok(["a", "b", "c"].includes(t1));
    seen.add(t1);
  }
  assert.ok(seen.size >= 2, "the hash should spread across tints");
});

// AC7/AC10: the URL a card renders — built-in, uploaded, cache-busted.

test("no image → no URL (the generated cover is drawn instead)", () => {
  assert.equal(templateCoverUrl(null, "2026-08-17T00:00:00Z", "https://x.supabase.co"), null);
  assert.equal(templateCoverUrl("", null, "https://x.supabase.co"), null);
});

test("a built-in cover is served from the app, never from Storage", () => {
  assert.equal(templateCoverUrl("builtin/web-app", null, undefined), "/template-covers/web-app.svg");
  assert.equal(
    templateCoverUrl("builtin/full-stack", "2026-08-17T00:00:00Z", "https://x.supabase.co"),
    "/template-covers/full-stack.svg",
  );
  assert.equal(isBuiltinCover("builtin/web-app"), true);
  assert.equal(isBuiltinCover("catalog/1/cover"), false);
  assert.equal(isBuiltinCover("builtin/Web App"), false);
  assert.equal(builtinCoverPath("site"), "builtin/site");
});

test("every shipped built-in name is a legal image_path", () => {
  for (const c of BUILTIN_COVERS) assert.equal(isBuiltinCover(builtinCoverPath(c.name)), true);
});

test("every registered built-in cover ships as public/template-covers/<name>.svg", () => {
  // us-118.5: a name in the registry with no file behind it is a card with a
  // broken image — on every row that picked it. Pin the two together.
  const dir = new URL("../../public/template-covers/", import.meta.url);
  for (const c of BUILTIN_COVERS) {
    assert.equal(
      existsSync(new URL(`${c.name}.svg`, dir)),
      true,
      `missing public/template-covers/${c.name}.svg`,
    );
  }
});

test("an uploaded cover is the bucket's public URL, cache-busted by updated_at", () => {
  const url = templateCoverUrl(
    "catalog/11111111-1111-1111-1111-111111111111/cover",
    "2026-08-17T10:00:00.000Z",
    "https://x.supabase.co/",
  );
  assert.equal(
    url,
    "https://x.supabase.co/storage/v1/object/public/template-images/catalog/11111111-1111-1111-1111-111111111111/cover?v=" +
      Date.parse("2026-08-17T10:00:00.000Z"),
  );
});

test("a replaced image changes the URL, an unchanged row does not", () => {
  const a = templateCoverUrl("o/t/cover", "2026-08-17T10:00:00Z", "https://x.supabase.co");
  const b = templateCoverUrl("o/t/cover", "2026-08-17T10:00:00Z", "https://x.supabase.co");
  const c = templateCoverUrl("o/t/cover", "2026-08-17T10:00:01Z", "https://x.supabase.co");
  assert.equal(a, b);
  assert.notEqual(a, c);
});

test("without a project URL a stored path cannot be resolved (and says so by null)", () => {
  assert.equal(templateCoverUrl("catalog/x/cover", null, undefined), null);
});

// AC6: the client-side check names the limit before any upload.

test("image problems: type and size, named", () => {
  assert.equal(templateImageProblem({ type: "image/png", size: 1000, name: "a.png" }), null);
  assert.equal(templateImageProblem({ type: "image/svg+xml", size: 1000, name: "a.svg" }), null);
  assert.match(templateImageProblem({ type: "application/pdf", size: 10, name: "a.pdf" }) ?? "", /PNG, JPEG, WebP, GIF or SVG/);
  assert.match(templateImageProblem({ type: "image/png", size: 3 * 1024 * 1024, name: "big.png" }) ?? "", /2 MB/);
});

// The object paths, and who owns which.

test("object paths match the DB CHECK shapes", () => {
  assert.equal(catalogCoverObject("abc"), "catalog/abc/cover");
  assert.equal(orgCoverObject("org1", "t1"), "org1/t1/cover");
});

test("an org owns only objects under its own folder", () => {
  assert.equal(isOwnOrgCover("org1/t1/cover", "org1"), true);
  assert.equal(isOwnOrgCover("catalog/t1/cover", "org1"), false);
  assert.equal(isOwnOrgCover("builtin/web-app", "org1"), false);
  assert.equal(isOwnOrgCover(null, "org1"), false);
  assert.equal(isOwnOrgCover("org10/t1/cover", "org1"), false);
});
