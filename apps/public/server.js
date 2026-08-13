// Tiny zero-dependency static file server for the Build Mill public site.
// Serves this directory (index.html + /assets) on PORT (default 3040).
// Run via `node server.js` or `npm start` — no build step.
const http = require("node:http");
const fs = require("node:fs");
const path = require("node:path");

const ROOT = __dirname;
const PORT = Number(process.env.PORT) || 3040;
const HOST = process.env.HOST || "0.0.0.0";

const CONTENT_TYPES = {
  ".html": "text/html; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".gif": "image/gif",
  ".svg": "image/svg+xml",
  ".ico": "image/x-icon",
  ".webp": "image/webp",
  ".woff": "font/woff",
  ".woff2": "font/woff2",
  ".txt": "text/plain; charset=utf-8",
  ".xml": "application/xml; charset=utf-8",
};

function send(res, status, body, headers = {}) {
  res.writeHead(status, headers);
  res.end(body);
}

const server = http.createServer((req, res) => {
  // Only GET/HEAD are meaningful for a static site.
  if (req.method !== "GET" && req.method !== "HEAD") {
    return send(res, 405, "Method Not Allowed", { Allow: "GET, HEAD" });
  }

  // Decode, strip query/hash, and resolve inside ROOT to block path traversal.
  let pathname;
  try {
    pathname = decodeURIComponent(new URL(req.url, "http://localhost").pathname);
  } catch {
    return send(res, 400, "Bad Request");
  }

  const relative = pathname === "/" ? "index.html" : pathname.replace(/^\/+/, "");
  const resolved = path.resolve(ROOT, relative);
  if (resolved !== ROOT && !resolved.startsWith(ROOT + path.sep)) {
    return send(res, 403, "Forbidden");
  }

  fs.stat(resolved, (err, stat) => {
    // Missing file or a directory → fall back to the single-page index.
    if (err || stat.isDirectory()) {
      return serveFile(res, path.join(ROOT, "index.html"), req.method, 200);
    }
    serveFile(res, resolved, req.method, 200);
  });
});

function serveFile(res, filePath, method, status) {
  const type = CONTENT_TYPES[path.extname(filePath).toLowerCase()] || "application/octet-stream";
  const isImmutable = filePath.includes(path.sep + "assets" + path.sep);
  const headers = {
    "Content-Type": type,
    "Cache-Control": isImmutable ? "public, max-age=3600" : "no-cache",
    "X-Content-Type-Options": "nosniff",
  };
  if (method === "HEAD") return send(res, status, null, headers);

  const stream = fs.createReadStream(filePath);
  stream.on("open", () => res.writeHead(status, headers));
  stream.on("error", () => send(res, 500, "Internal Server Error"));
  stream.pipe(res);
}

server.listen(PORT, HOST, () => {
  console.log(`buildmill public site listening on http://${HOST}:${PORT}`);
});
