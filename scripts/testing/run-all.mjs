#!/usr/bin/env node
/**
 * The common runner: one Playwright pass over every category, one summary.
 *
 * Playwright's own output is per-test and scrolls; what you actually want after
 * pointing this at a deployment is "which categories are healthy, which are
 * not, and what exactly failed". So this drives `playwright test` with the JSON
 * reporter, folds the result back by spec file — one file per API category —
 * and prints that, plus the failures in full and the skips grouped by the
 * reason they were skipped (almost always a credential nobody configured).
 *
 * Usage
 *   node run-all.mjs                      every category
 *   node run-all.mjs admin worker         only those categories
 *   node run-all.mjs --list               what categories exist
 *   node run-all.mjs --base-url=https://api.buildmill.dev
 *   node run-all.mjs --mutations          allow state-changing tests
 *   node run-all.mjs --workers=2          throttle concurrency
 *
 * Exit code is 0 only when nothing failed — skips do not fail a run.
 */

import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const TESTS_DIR = path.join(HERE, "tests");
const RESULTS_DIR = path.join(HERE, "results");

const COLUMNS = process.stdout.columns ?? 80;
const useColor = process.stdout.isTTY && !process.env.NO_COLOR;
const ESC = String.fromCharCode(27);
const paint = (code, text) =>
  useColor ? `${ESC}[${code}m${text}${ESC}[0m` : text;
const green = (t) => paint("32", t);
const red = (t) => paint("31", t);
const yellow = (t) => paint("33", t);
const dim = (t) => paint("2", t);
const bold = (t) => paint("1", t);

function categories() {
  return fs
    .readdirSync(TESTS_DIR)
    .filter((f) => f.endsWith(".spec.ts"))
    .map((f) => f.replace(/\.spec\.ts$/, ""))
    .sort();
}

function parseArgs(argv) {
  const options = { selected: [], list: false, passthrough: [] };
  for (const arg of argv) {
    if (arg === "--list") options.list = true;
    else if (arg === "--mutations") process.env.ALLOW_MUTATIONS = "1";
    else if (arg.startsWith("--base-url=")) process.env.API_BASE_URL = arg.slice(11);
    else if (arg.startsWith("--workers=")) process.env.PW_WORKERS = arg.slice(10);
    else if (arg.startsWith("--")) options.passthrough.push(arg);
    else options.selected.push(arg);
  }
  return options;
}

/**
 * Walk the JSON report's nested suites and yield every spec, attributed to the
 * spec file that OWNS it — the root suite — not to wherever the `test()` call
 * physically lives. The generated auth-boundary tests are declared inside
 * lib/suite.ts, so trusting `spec.file` files 224 of them under a phantom
 * "suite" category instead of under the categories that asked for them.
 */
function* walkSpecs(suite, file = suite.file ?? suite.title) {
  for (const spec of suite.specs ?? []) yield { spec, file };
  for (const child of suite.suites ?? []) yield* walkSpecs(child, file);
}

function summarize(report) {
  const byCategory = new Map();
  const failures = [];
  const skips = new Map();

  for (const rootSuite of report.suites ?? []) {
    for (const { spec, file } of walkSpecs(rootSuite)) {
      const category = path
        .basename(file ?? "unknown")
        .replace(/\.spec\.ts$/, "");
      if (!byCategory.has(category)) {
        byCategory.set(category, {
          total: 0,
          passed: 0,
          failed: 0,
          skipped: 0,
          durationMs: 0,
        });
      }
      const row = byCategory.get(category);

      for (const test of spec.tests ?? []) {
        row.total += 1;
        const result = test.results?.[test.results.length - 1] ?? {};
        row.durationMs += result.duration ?? 0;

        // `status` on the test is Playwright's verdict against the expectation
        // ('expected' / 'unexpected' / 'skipped' / 'flaky'); the result's own
        // status is the raw outcome. The verdict is what a summary wants.
        if (test.status === "skipped" || result.status === "skipped") {
          row.skipped += 1;
          const reason =
            (result.errors ?? [])[0]?.message ??
            spec.annotations?.find((a) => a.type === "skip")?.description ??
            test.annotations?.find((a) => a.type === "skip")?.description ??
            "skipped";
          const key = String(reason).split("\n")[0].trim();
          skips.set(key, (skips.get(key) ?? 0) + 1);
        } else if (test.status === "unexpected" || result.status === "failed" || result.status === "timedOut") {
          row.failed += 1;
          failures.push({
            category,
            title: spec.title,
            message: (result.error?.message ?? result.errors?.[0]?.message ?? "no error message")
              .replace(new RegExp(String.fromCharCode(27)+'\[[0-9;]*m', 'g'), "")
              .trim(),
          });
        } else {
          row.passed += 1;
        }
      }
    }
  }
  return { byCategory, failures, skips };
}

function renderTable(byCategory) {
  const names = [...byCategory.keys()].sort();
  const width = Math.max(8, ...names.map((n) => n.length));
  const head =
    "  " +
    bold("CATEGORY".padEnd(width)) +
    bold("  TOTAL") +
    bold("   PASS") +
    bold("   FAIL") +
    bold("   SKIP") +
    bold("      TIME");
  const lines = [head, dim("  " + "─".repeat(Math.min(COLUMNS - 4, width + 38)))];

  const totals = { total: 0, passed: 0, failed: 0, skipped: 0, durationMs: 0 };
  for (const name of names) {
    const row = byCategory.get(name);
    for (const key of Object.keys(totals)) totals[key] += row[key];
    lines.push(
      "  " +
        name.padEnd(width) +
        String(row.total).padStart(7) +
        (row.passed ? green(String(row.passed).padStart(7)) : String(row.passed).padStart(7)) +
        (row.failed ? red(String(row.failed).padStart(7)) : dim(String(row.failed).padStart(7))) +
        (row.skipped ? yellow(String(row.skipped).padStart(7)) : dim(String(row.skipped).padStart(7))) +
        dim(`${(row.durationMs / 1000).toFixed(1)}s`.padStart(10)),
    );
  }
  lines.push(dim("  " + "─".repeat(Math.min(COLUMNS - 4, width + 38))));
  lines.push(
    "  " +
      bold("TOTAL".padEnd(width)) +
      String(totals.total).padStart(7) +
      green(String(totals.passed).padStart(7)) +
      (totals.failed ? red(String(totals.failed).padStart(7)) : dim("0".padStart(7))) +
      (totals.skipped ? yellow(String(totals.skipped).padStart(7)) : dim("0".padStart(7))) +
      dim(`${(totals.durationMs / 1000).toFixed(1)}s`.padStart(10)),
  );
  return { lines, totals };
}

function markdown(byCategory, totals, failures, skips, meta) {
  const rows = [...byCategory.keys()]
    .sort()
    .map((name) => {
      const r = byCategory.get(name);
      return `| ${name} | ${r.total} | ${r.passed} | ${r.failed} | ${r.skipped} | ${(r.durationMs / 1000).toFixed(1)}s |`;
    })
    .join("\n");

  return [
    `# API test summary`,
    ``,
    `- Target: \`${meta.baseUrl}\``,
    `- Run at: ${meta.startedAt}`,
    `- Mutations: ${meta.mutations ? "enabled" : "disabled"}`,
    ``,
    `| Category | Total | Passed | Failed | Skipped | Time |`,
    `| --- | ---: | ---: | ---: | ---: | ---: |`,
    rows,
    `| **Total** | **${totals.total}** | **${totals.passed}** | **${totals.failed}** | **${totals.skipped}** | **${(totals.durationMs / 1000).toFixed(1)}s** |`,
    ``,
    failures.length ? `## Failures\n` : `## Failures\n\nNone.\n`,
    ...failures.map(
      (f) => `### ${f.category} — ${f.title}\n\n\`\`\`\n${f.message}\n\`\`\`\n`,
    ),
    skips.size ? `## Skipped, by reason\n` : "",
    ...[...skips.entries()]
      .sort((a, b) => b[1] - a[1])
      .map(([reason, count]) => `- ${count} × ${reason}`),
    ``,
  ].join("\n");
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  const available = categories();

  if (options.list) {
    console.log(`${available.length} categories:\n`);
    for (const name of available) console.log(`  ${name}`);
    return 0;
  }

  const unknown = options.selected.filter((c) => !available.includes(c));
  if (unknown.length) {
    console.error(`Unknown categor${unknown.length > 1 ? "ies" : "y"}: ${unknown.join(", ")}`);
    console.error(`Available: ${available.join(", ")}`);
    return 2;
  }

  fs.mkdirSync(RESULTS_DIR, { recursive: true });
  const jsonPath = path.join(
    fs.mkdtempSync(path.join(os.tmpdir(), "bm-api-tests-")),
    "report.json",
  );

  // No `--reporter=json` here: a CLI reporter flag REPLACES the config's
  // reporter list, taking `outputFile` with it — the report then goes to
  // stdout, thousands of lines of it, and this runner finds no file to read.
  // The config switches itself to json+line when PW_JSON_OUTPUT is set.
  const args = ["playwright", "test"];
  for (const category of options.selected) args.push(`tests/${category}.spec.ts`);
  args.push(...options.passthrough);

  const startedAt = new Date().toISOString();
  const started = Date.now();
  const exitCode = await new Promise((resolve) => {
    const child = spawn(process.platform === "win32" ? "npx.cmd" : "npx", args, {
      cwd: HERE,
      env: { ...process.env, PW_JSON_OUTPUT: jsonPath },
      // Playwright's own line reporter still streams to the terminal (the
      // config adds it alongside JSON), so a long run is not a silent one.
      stdio: ["ignore", "inherit", "inherit"],
      shell: process.platform === "win32",
    });
    child.on("error", (error) => {
      console.error(`\nCould not start Playwright: ${error.message}`);
      console.error("Run `npm install` in scripts/testing first.");
      resolve(127);
    });
    child.on("close", resolve);
  });

  if (!fs.existsSync(jsonPath)) {
    console.error("\nNo JSON report was produced — see the Playwright output above.");
    return exitCode || 1;
  }

  const report = JSON.parse(fs.readFileSync(jsonPath, "utf8"));
  const { byCategory, failures, skips } = summarize(report);
  const { lines, totals } = renderTable(byCategory);

  const baseUrl = process.env.API_BASE_URL ?? "http://localhost:8000";
  console.log("\n" + bold("  API TEST SUMMARY"));
  console.log(dim(`  ${baseUrl} · ${((Date.now() - started) / 1000).toFixed(1)}s wall clock\n`));
  console.log(lines.join("\n"));

  if (failures.length) {
    console.log("\n" + bold(red(`  ${failures.length} failure${failures.length > 1 ? "s" : ""}`)));
    for (const failure of failures) {
      console.log(`\n  ${red("✗")} ${bold(failure.category)} › ${failure.title}`);
      for (const line of failure.message.split("\n").slice(0, 8)) {
        console.log(dim(`      ${line}`));
      }
    }
  }

  if (skips.size) {
    console.log("\n" + bold(yellow("  Skipped, by reason")));
    for (const [reason, count] of [...skips.entries()].sort((a, b) => b[1] - a[1])) {
      console.log(`      ${yellow(String(count).padStart(4))} × ${reason}`);
    }
    console.log(
      dim("\n  Skips are unconfigured credentials or opt-in mutations, not passes."),
    );
  }

  const meta = { baseUrl, startedAt, mutations: process.env.ALLOW_MUTATIONS === "1" };
  fs.writeFileSync(
    path.join(RESULTS_DIR, "summary.json"),
    JSON.stringify(
      { ...meta, totals, categories: Object.fromEntries(byCategory), failures },
      null,
      2,
    ),
    "utf8",
  );
  fs.writeFileSync(
    path.join(RESULTS_DIR, "summary.md"),
    markdown(byCategory, totals, failures, skips, meta),
    "utf8",
  );
  console.log(dim(`\n  Written: results/summary.md, results/summary.json`));

  const verdict = totals.failed
    ? red(`  FAILED — ${totals.failed} of ${totals.total}`)
    : green(`  PASSED — ${totals.passed} of ${totals.total}`);
  console.log("\n" + bold(verdict) + "\n");

  return totals.failed ? 1 : 0;
}

process.exitCode = await main();
