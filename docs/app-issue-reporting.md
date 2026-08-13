# Reporting issues from a deployed app

A deployment can report two kinds of thing into Build Mill: crashes it catches
itself, and problems its users write up by hand. Both land in the same inbox
(the **Reports** hub), deduplicated, and neither becomes a work item until a
manager promotes it.

Everything below needs one thing first: open the deployment in Build Mill
(*Projects → your project → Deployments → the deployment*), turn on **Issue
reporting**, and press **Show** to reveal the key. The two snippets on that
card already have your deployment id, key and endpoint substituted in — copying
from there is less error-prone than copying from here.

Reporting is **per deployment**, not per project. UAT and Production hold
different keys on purpose, so a manager can tell which environment is on fire.

## Automatic crash reporting (browser)

```html
<script src="https://<build-mill-host>/embed/report-sdk.js"
        data-deployment="<deployment-id>"
        data-key="<report-key>"
        data-endpoint="https://<api-host>/api/v1/report"
        async></script>
```

That is the whole setup. The SDK installs `window.onerror` and
`unhandledrejection` listeners and posts what they catch, with the page URL and
user agent as context.

It will never break the app it is watching: every failure inside it is
swallowed, nothing is awaited, and it never re-throws into one of your
handlers. If Build Mill is unreachable, you lose the report — not the page.

To report an error your own code already caught:

```js
window.buildmillReport(error, { checkout_step: "payment" });
```

## Automatic crash reporting (Node)

A server process cannot use a `<script>` tag, so call the endpoint directly.
The contract is the same:

```js
const ENDPOINT = "https://<api-host>/api/v1/report/<deployment-id>/issues";
const KEY = process.env.BUILDMILL_REPORT_KEY;

function report(error) {
  fetch(ENDPOINT, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Report-Key": KEY },
    body: JSON.stringify({
      source: "automated",
      error_type: error.name,
      message: error.message,
      stack_trace: error.stack,
      context: { service: "api", version: process.env.APP_VERSION },
    }),
  }).catch(() => {});           // never let reporting throw
}

process.on("uncaughtException", (e) => { report(e); });
process.on("unhandledRejection", (e) => { report(e); });
```

Keep the key in the environment, not in source. It is not a Vault-tier secret
— it ships inside browser bundles by design — but it is still a credential
that can write into your inbox.

## The "Report an issue" widget

```html
<script src="https://<build-mill-host>/embed/report-widget.js"
        data-deployment="<deployment-id>"
        data-key="<report-key>"
        data-endpoint="https://<api-host>/api/v1/report"
        data-position="bottom-right"
        async></script>
```

Adds a small trigger in the corner that opens a form: a required description,
an optional name and email. Anonymous reports are allowed.

- `data-position` — `bottom-right` (default), `bottom-left`, `top-right`,
  `top-left`.
- `data-label` — the trigger's text, default *Report an issue*.

Styling is inline and self-contained, so it cannot collide with your app's CSS.
Unlike the crash SDK, the widget shows the person whether their report arrived
— somebody is waiting on the answer.

## The HTTP contract

If neither script fits (a non-JS runtime, a mobile app, your own UI), post the
same thing yourself:

```
POST <api-host>/api/v1/report/<deployment-id>/issues
X-Report-Key: <report-key>
Content-Type: application/json
```

```json
{
  "source": "automated",
  "error_type": "TypeError",
  "message": "Cannot read properties of undefined",
  "stack_trace": "...",
  "context": { "url": "...", "app_version": "1.4.2" }
}
```

```json
{
  "source": "user_report",
  "title": "Checkout button does nothing",
  "message": "...",
  "reporter_name": "...",
  "reporter_email": "...",
  "context": { "url": "..." }
}
```

Answers `201 {"id": "...", "status": "accepted"}`.

**`401 {"detail": "invalid report key"}`** means one of: the key is wrong, the
deployment id is unknown, or issue reporting is switched off for it. The
endpoint deliberately does not say which — that is what stops it being used to
discover which deployments exist. Check the toggle before you suspect the key.

**`429`** means you are over the per-deployment rate limit. Repeats of one
crash are collapsed server-side by fingerprint, so hitting this usually means
genuinely novel errors arriving faster than a human could ever triage them.

Some notes on what happens to what you send:

- **Repeats collapse.** Automated reports are fingerprinted on the error type,
  a normalized message and the top three stack frames. The same crash a
  thousand times is one row with a count of a thousand — and timestamps, UUIDs,
  hex addresses and long numbers are stripped from the message before hashing,
  so a crash loop that embeds a request id still dedupes.
- **A closed report does not reopen.** Once a report is promoted, ignored or
  fixed, the same crash arriving again opens a *fresh* row. A regression is a
  new bug, not a counter ticking on a closed one.
- **Oversized fields are truncated, not rejected** — a huge stack trace still
  records something usable, and says where it was cut.
- **Rotating the key takes effect immediately.** There is no grace period: any
  app still carrying the old key stops reporting silently until you redeploy
  it. Disabling the toggle is the reversible way to stop ingestion.
