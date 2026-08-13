"use client";

// Replaces the root layout when it fails, so everything here must be
// self-contained: own <html>/<body>, own stylesheet import, no ThemeProvider
// (dark mode falls back to the prefers-color-scheme override below), no
// next/font (system stack instead).
import "./globals.css";
import { useEffect } from "react";
import { reloadOnceForNetworkError, reportSelfError } from "@/lib/self-report";

export default function GlobalError({
  error,
}: {
  error: Error & { digest?: string };
}) {
  // US-16.8: the root layout failing is the worst thing that happens to this
  // app, so it is the one most worth recording. The reporter is deliberately
  // the only app machinery this page touches — it has no imports of its own
  // beyond the API base URL, and it swallows everything.
  useEffect(() => {
    reportSelfError(error, { boundary: "global" });
    // US-79.4: a network/stale-chunk failure gets one guarded reload before
    // the jam screen — after a deploy, the reload IS the fix. The report
    // above rides keepalive, so it is not lost to the navigation.
    reloadOnceForNetworkError(error);
  }, [error]);

  return (
    <html lang="en">
      <body
        className="flex min-h-screen flex-col items-center justify-center gap-6 bg-background p-6 text-center text-foreground"
        style={{ fontFamily: "ui-sans-serif, system-ui, sans-serif" }}
      >
        <title>Something jammed — Build Mill</title>
        <style>{`@media (prefers-color-scheme: dark) { :root { --background: oklch(0.145 0 0); --foreground: oklch(0.985 0 0); --muted: oklch(0.269 0 0); --muted-foreground: oklch(0.708 0 0); --primary: oklch(0.922 0 0); --primary-foreground: oklch(0.205 0 0); } }`}</style>
        <div className="rounded-2xl bg-[#f6f6f6] px-4 py-3">
          {/* Plain <img>: keep this page free of app machinery. */}
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src="/buildmill-logo.png"
            alt="Build Mill logo"
            className="h-20 w-auto object-contain"
          />
        </div>
        <div className="max-w-md space-y-2">
          <h1 className="text-lg font-semibold">
            Something jammed on the line.
          </h1>
          <p className="text-sm text-muted-foreground">
            An unexpected error stopped Build Mill. Reload the page to get back
            to work.
          </p>
        </div>
        <button
          onClick={() => window.location.reload()}
          className="inline-flex h-8 items-center justify-center rounded-lg bg-primary px-2.5 text-sm font-medium text-primary-foreground hover:bg-primary/80"
        >
          Reload
        </button>
        {error.digest && (
          <p className="text-xs text-muted-foreground/70">
            Reference: {error.digest}
          </p>
        )}
      </body>
    </html>
  );
}
