/*!
 * Build Mill feedback widget (US-16.5)
 *
 *   <script src="https://<build-mill>/embed/report-widget.js"
 *           data-deployment="<deployment-id>"
 *           data-key="<report-key>"
 *           data-endpoint="https://<api>/api/v1/report"
 *           data-position="bottom-right"
 *           async></script>
 *
 * Unlike the error-capture SDK, this one has a human waiting on it: a person
 * who took the trouble to write out what went wrong must be told whether it
 * arrived. So failures here are shown, not swallowed — the swallow-everything
 * rule applies to the *host app's* stability, and it still holds: nothing
 * below throws outward.
 *
 * Styling is inline and namespaced so dropping it into an arbitrary app
 * cannot collide with that app's own CSS.
 */
(function () {
  "use strict";

  var script = document.currentScript;
  if (!script) return;

  var deployment = script.getAttribute("data-deployment");
  var key = script.getAttribute("data-key");
  var endpoint = script.getAttribute("data-endpoint");
  if (!deployment || !key || !endpoint) return;

  var position = script.getAttribute("data-position") || "bottom-right";
  var label = script.getAttribute("data-label") || "Report an issue";
  var url = endpoint.replace(/\/+$/, "") + "/" + deployment + "/issues";

  var CORNERS = {
    "bottom-right": "bottom:16px;right:16px;",
    "bottom-left": "bottom:16px;left:16px;",
    "top-right": "top:16px;right:16px;",
    "top-left": "top:16px;left:16px;",
  };
  var corner = CORNERS[position] || CORNERS["bottom-right"];

  var FONT =
    "font-family:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;font-size:14px;";
  var open = false;

  function el(tag, style, text) {
    var node = document.createElement(tag);
    node.setAttribute("style", style);
    if (text) node.textContent = text;
    return node;
  }

  var root = el(
    "div",
    "position:fixed;z-index:2147483000;" + corner + FONT + "line-height:1.4;",
  );

  var trigger = el(
    "button",
    "cursor:pointer;border:1px solid rgba(0,0,0,.15);border-radius:9999px;" +
      "background:#111;color:#fff;padding:10px 16px;box-shadow:0 2px 8px rgba(0,0,0,.2);" +
      FONT,
    label,
  );
  trigger.setAttribute("type", "button");

  var panel = el(
    "div",
    "display:none;width:320px;max-width:calc(100vw - 32px);background:#fff;color:#111;" +
      "border:1px solid rgba(0,0,0,.15);border-radius:12px;padding:16px;" +
      "box-shadow:0 8px 32px rgba(0,0,0,.24);margin-bottom:8px;" + FONT,
  );

  var heading = el("div", "font-weight:600;margin-bottom:8px;", "Tell us what happened");
  var description = document.createElement("textarea");
  description.setAttribute("rows", "4");
  description.setAttribute("placeholder", "What went wrong?");
  description.setAttribute(
    "style",
    "width:100%;box-sizing:border-box;resize:vertical;padding:8px;border:1px solid rgba(0,0,0,.2);border-radius:8px;" +
      FONT,
  );

  function input(placeholder) {
    var node = document.createElement("input");
    node.setAttribute("type", "text");
    node.setAttribute("placeholder", placeholder);
    node.setAttribute(
      "style",
      "width:100%;box-sizing:border-box;margin-top:8px;padding:8px;border:1px solid rgba(0,0,0,.2);border-radius:8px;" +
        FONT,
    );
    return node;
  }
  var name = input("Your name (optional)");
  var email = input("Your email (optional)");

  var status = el("div", "margin-top:8px;min-height:18px;font-size:13px;color:#555;");
  var actions = el("div", "display:flex;gap:8px;margin-top:12px;justify-content:flex-end;");
  var cancel = el(
    "button",
    "cursor:pointer;background:transparent;border:1px solid rgba(0,0,0,.2);border-radius:8px;padding:8px 12px;" +
      FONT,
    "Cancel",
  );
  var submit = el(
    "button",
    "cursor:pointer;background:#111;color:#fff;border:none;border-radius:8px;padding:8px 14px;" + FONT,
    "Send",
  );
  cancel.setAttribute("type", "button");
  submit.setAttribute("type", "button");

  actions.appendChild(cancel);
  actions.appendChild(submit);
  panel.appendChild(heading);
  panel.appendChild(description);
  panel.appendChild(name);
  panel.appendChild(email);
  panel.appendChild(status);
  panel.appendChild(actions);
  root.appendChild(panel);
  root.appendChild(trigger);

  function toggle(next) {
    open = next;
    panel.style.display = next ? "block" : "none";
    if (next) description.focus();
  }

  function reset() {
    description.value = "";
    name.value = "";
    email.value = "";
    status.textContent = "";
    submit.disabled = false;
    submit.textContent = "Send";
  }

  trigger.addEventListener("click", function () {
    toggle(!open);
  });
  cancel.addEventListener("click", function () {
    toggle(false);
    reset();
  });

  submit.addEventListener("click", function () {
    var text = description.value.trim();
    if (!text) {
      // The description is the only required field — anonymous is fine.
      status.style.color = "#b00";
      status.textContent = "Please describe what happened.";
      return;
    }
    submit.disabled = true;
    submit.textContent = "Sending…";
    status.style.color = "#555";
    status.textContent = "";

    var firstLine = text.split("\n")[0];
    var payload = {
      source: "user_report",
      title: firstLine.length > 120 ? firstLine.slice(0, 117) + "…" : firstLine,
      message: text,
      reporter_name: name.value.trim() || null,
      reporter_email: email.value.trim() || null,
      context: {
        url: location.href,
        user_agent: navigator.userAgent,
        reported_by: "report-widget",
      },
    };

    fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Report-Key": key },
      body: JSON.stringify(payload),
      mode: "cors",
      credentials: "omit",
    })
      .then(function (response) {
        if (!response.ok) throw new Error("HTTP " + response.status);
        // A person is waiting on this: say it landed.
        status.style.color = "#0a7";
        status.textContent = "Thanks — we got it.";
        submit.textContent = "Sent";
        setTimeout(function () {
          toggle(false);
          reset();
        }, 1600);
      })
      .catch(function () {
        // ...and say when it did not, rather than pretending success.
        status.style.color = "#b00";
        status.textContent = "That did not send. Please try again.";
        submit.disabled = false;
        submit.textContent = "Send";
      });
  });

  function mount() {
    if (document.body) document.body.appendChild(root);
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mount);
  } else {
    mount();
  }
})();
