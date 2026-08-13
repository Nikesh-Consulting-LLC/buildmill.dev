/*!
 * Build Mill error-capture SDK (US-16.4)
 *
 * Drop-in, dependency-free, no build step:
 *
 *   <script src="https://<build-mill>/embed/report-sdk.js"
 *           data-deployment="<deployment-id>"
 *           data-key="<report-key>"
 *           data-endpoint="https://<api>/api/v1/report"
 *           async></script>
 *
 * The one rule this file obeys above all others: it must never break the app
 * it is watching. Every failure inside it is swallowed, nothing it does is
 * awaited, and it never re-throws into a host handler. An app that cannot
 * report is an app with a reporting problem, not an app with an outage.
 *
 * Node: see the setup doc — a server process cannot use a <script> tag, so it
 * calls the same endpoint from process.on('uncaughtException').
 */
(function () {
  "use strict";

  var script = document.currentScript;
  if (!script) return;

  var deployment = script.getAttribute("data-deployment");
  var key = script.getAttribute("data-key");
  var endpoint = script.getAttribute("data-endpoint");
  if (!deployment || !key || !endpoint) return;

  var url = endpoint.replace(/\/+$/, "") + "/" + deployment + "/issues";

  // Reporting the failure of reporting is how an SDK takes an app down. The
  // guard is not defensive tidiness — it is the difference between one dropped
  // report and an unbounded loop.
  var reporting = false;

  // Cheap client-side repeat suppression. The server dedupes properly by
  // fingerprint; this only stops a tight loop from spending the user's network
  // on a thousand identical POSTs before the server ever sees them.
  var seen = Object.create(null);
  var SUPPRESS_AFTER = 5;

  function send(payload) {
    if (reporting) return;
    reporting = true;
    try {
      var signature = String(payload.error_type) + "|" + String(payload.message);
      seen[signature] = (seen[signature] || 0) + 1;
      if (seen[signature] > SUPPRESS_AFTER) return;

      payload.source = "automated";
      payload.context = payload.context || {};
      payload.context.url = location.href;
      payload.context.user_agent = navigator.userAgent;
      payload.context.reported_by = "report-sdk";

      var body = JSON.stringify(payload);

      // fetch with keepalive so a report survives the page unloading right
      // after the error that caused it — which is the common case.
      if (typeof fetch === "function") {
        fetch(url, {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-Report-Key": key },
          body: body,
          keepalive: true,
          mode: "cors",
          credentials: "omit",
        }).catch(function () {
          /* best effort, by design */
        });
      } else {
        var xhr = new XMLHttpRequest();
        xhr.open("POST", url, true);
        xhr.setRequestHeader("Content-Type", "application/json");
        xhr.setRequestHeader("X-Report-Key", key);
        xhr.onerror = function () {};
        xhr.send(body);
      }
    } catch (e) {
      /* swallowed: the SDK's own failure is never the app's problem */
    } finally {
      reporting = false;
    }
  }

  function describe(error) {
    if (error && typeof error === "object") {
      return {
        error_type: error.name || "Error",
        message: error.message || String(error),
        stack_trace: error.stack || null,
      };
    }
    return { error_type: "Error", message: String(error), stack_trace: null };
  }

  window.addEventListener("error", function (event) {
    try {
      var described = event.error
        ? describe(event.error)
        : {
            error_type: "Error",
            message: event.message || "Unknown error",
            stack_trace: null,
          };
      described.context = {
        source_file: event.filename || null,
        line: event.lineno || null,
        column: event.colno || null,
      };
      send(described);
    } catch (e) {
      /* swallowed */
    }
  });

  window.addEventListener("unhandledrejection", function (event) {
    try {
      var described = describe(event.reason);
      described.error_type = "UnhandledRejection: " + described.error_type;
      send(described);
    } catch (e) {
      /* swallowed */
    }
  });

  // Deliberate reporting, for a host app that catches its own errors and still
  // wants them recorded. Same swallow-everything contract.
  window.buildmillReport = function (error, context) {
    try {
      var described = describe(error);
      described.context = context || {};
      send(described);
    } catch (e) {
      /* swallowed */
    }
  };
})();
