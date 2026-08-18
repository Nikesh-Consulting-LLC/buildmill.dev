// US-103.3: which release states a Stop can end.
//
// Deliberately NOT in stop-release-button.tsx, and this file deliberately has
// no "use client". It shipped there on 2026-08-16 and crashed the /releases
// page in production within a minute of the deploy: `releases/page.tsx` is a
// Server Component, and every export of a "use client" module — a Set very
// much included — reaches the server as a client *reference*, not the value.
// `STOPPABLE.has(...)` therefore threw during the server render. Nothing
// caught it: `next build` type-checks the import fine, because the types are
// real even when the runtime value is not.
//
// The rule this file encodes: a value a Server Component reads lives in a
// module with no "use client" directive. Components may be imported across
// that boundary; data may not.
//
// US-119.1 adds `deploying`: a release whose UAT deploy died sat there all
// day on 2026-08-18 with no button on any page. Stop at `deploying` cancels
// the deploy run (cooperatively when it is live) and stops the release.
// Mirrors STOPPABLE in apps/api/app/routers/releases.py.
export const STOPPABLE = new Set([
  "queued",
  "running",
  "notes-ready",
  "deploying",
  "uat-deploy-failed",
]);
