// A Server Component may import a client COMPONENT. It may not read a client
// module's DATA.
//
// On 2026-08-16 `releases/page.tsx` (a Server Component) imported `STOPPABLE`
// — a plain `Set` — from `stop-release-button.tsx`, which carries
// "use client". Every export of a "use client" module reaches the server as a
// client *reference*, not the value, so `STOPPABLE.has(...)` threw during the
// server render and the /releases page 500'd in production within a minute of
// the deploy.
//
// Nothing caught it. `next build` type-checks that import perfectly happily —
// the TYPES are real even when the runtime value is not — and the page has no
// unit test. This is the check that would have.
//
// The rule: in a file with no "use client", a binding imported from a file
// that HAS one may only be used as JSX (`<Name ... />`). The moment it is
// dotted into, called, spread or indexed, it is being read as data on the
// server, which cannot work.

import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { test } from "node:test";

const SRC = resolve(import.meta.dirname, "..");

function walk(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    if (entry === "node_modules" || entry === ".next") continue;
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) walk(full, out);
    else if (/\.tsx?$/.test(entry) && !/\.test\.tsx?$/.test(entry)) out.push(full);
  }
  return out;
}

const isClientModule = (file: string) =>
  /^\s*(["'])use client\1/.test(readFileSync(file, "utf8"));

/** Resolve a relative or `@/`-rooted specifier to a file on disk. */
function resolveLocal(fromFile: string, spec: string): string | null {
  let base: string;
  if (spec.startsWith(".")) base = resolve(dirname(fromFile), spec);
  else if (spec.startsWith("@/")) base = resolve(SRC, spec.slice(2));
  else return null; // a package, not ours
  for (const candidate of [
    `${base}.ts`,
    `${base}.tsx`,
    join(base, "index.ts"),
    join(base, "index.tsx"),
  ]) {
    try {
      if (statSync(candidate).isFile()) return candidate;
    } catch {
      /* keep looking */
    }
  }
  return null;
}

/** `import { A, B as C } from "x"` / `import D from "x"` → the local names. */
function importedNames(clause: string): string[] {
  const names: string[] = [];
  const braced = clause.match(/\{([^}]*)\}/);
  if (braced) {
    for (const part of braced[1].split(",")) {
      const bits = part.trim().split(/\s+as\s+/);
      const local = (bits[1] ?? bits[0]).trim();
      if (local) names.push(local);
    }
  }
  const beforeBrace = clause.split("{")[0].replace(/^\s*type\s+/, "").trim();
  const dflt = beforeBrace.replace(/,$/, "").trim();
  if (dflt && !dflt.startsWith("*")) names.push(dflt);
  return names;
}

const IMPORT = /import\s+(type\s+)?([\s\S]*?)\s+from\s+["']([^"']+)["']/g;

/** Files the server definitely renders: App Router entrypoints without
 * "use client", plus everything they reach before crossing the boundary.
 *
 * The distinction matters and a cruder rule gets it wrong. A module with no
 * "use client" is NOT automatically a Server Component — it inherits the
 * context of whoever imports it, so a shared helper pulled in only by client
 * components runs on the client and may touch client-only values freely. Only
 * modules actually reachable from a server entrypoint are bound by the rule.
 */
function serverReachable(): Set<string> {
  const seen = new Set<string>();
  const queue: string[] = [];

  for (const file of walk(SRC)) {
    const name = file.slice(file.lastIndexOf("\\") + 1).replace(/^.*\//, "");
    if (/^(page|layout|template|default|route)\.tsx?$/.test(name)) {
      if (!isClientModule(file)) queue.push(file);
    }
  }

  while (queue.length) {
    const file = queue.pop()!;
    if (seen.has(file)) continue;
    seen.add(file);
    for (const match of readFileSync(file, "utf8").matchAll(IMPORT)) {
      const [, typeOnly, , spec] = match;
      if (typeOnly) continue;
      const target = resolveLocal(file, spec);
      // Stop at the boundary: past a "use client" module everything is
      // client, and its own imports are none of this check's business.
      if (target && !isClientModule(target)) queue.push(target);
    }
  }
  return seen;
}

test("a server component never reads data out of a client module", () => {
  const offenders: string[] = [];

  for (const file of serverReachable()) {
    const source = readFileSync(file, "utf8");

    for (const match of source.matchAll(IMPORT)) {
      const [, typeOnly, clause, spec] = match;
      if (typeOnly) continue; // types are erased; they never run
      const target = resolveLocal(file, spec);
      if (!target || !isClientModule(target)) continue;

      for (const name of importedNames(clause)) {
        // Used as data if it is dotted into, called, indexed or spread.
        const asData = new RegExp(
          `\\b${name}\\s*(\\.|\\(|\\[)|\\.\\.\\.${name}\\b`
        );
        // `<Name` and `render={<Name />}` are the legitimate uses.
        const stripped = source.replace(new RegExp(`<${name}\\b`, "g"), "");
        if (asData.test(stripped)) {
          offenders.push(
            `${file.slice(SRC.length + 1)} reads \`${name}\` from the client ` +
              `module ${spec} — move that value into a module with no ` +
              `"use client", or the server render throws.`
          );
        }
      }
    }
  }

  assert.deepEqual(offenders, [], "\n" + offenders.join("\n"));
});

test("the check can actually see the tree it is checking", () => {
  // A wrong SRC or an empty reachable set would make the assertion above
  // vacuously true — which is how a guard quietly stops guarding.
  const files = walk(SRC);
  assert.ok(files.length > 100, `only found ${files.length} source files`);
  assert.ok(files.some(isClientModule), "no client modules found at all");

  const reachable = serverReachable();
  assert.ok(reachable.size > 20, `only ${reachable.size} server-reachable files`);
  // The page that actually crashed must be in scope, or this proves nothing.
  assert.ok(
    [...reachable].some((f) => f.replace(/\\/g, "/").endsWith("(app)/releases/page.tsx")),
    "the /releases page is not in the server-reachable set"
  );
});

test("STOPPABLE is readable from a server component", () => {
  // The specific regression: the constant the /releases page reads must not
  // live behind the client boundary.
  const states = join(SRC, "app", "(app)", "releases", "release-states.ts");
  assert.equal(isClientModule(states), false, "release-states.ts went client");
  assert.match(readFileSync(states, "utf8"), /export const STOPPABLE/);
});
