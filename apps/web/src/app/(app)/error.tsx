"use client";

import { useEffect } from "react";
import Link from "next/link";
import { Cog } from "lucide-react";
import { Button } from "@/components/ui/button";
import { TroubleScreen } from "@/components/trouble-screen";
import { reloadOnceForNetworkError, reportSelfError } from "@/lib/self-report";

export default function AppError({
  error,
  unstable_retry,
  reset,
}: {
  error: Error & { digest?: string };
  unstable_retry?: () => void;
  reset?: () => void;
}) {
  useEffect(() => {
    console.error(error);
    // US-16.8: a render that threw is a system error, so it is recorded rather
    // than only logged into a console nobody is watching.
    reportSelfError(error, { boundary: "app-route" });
    // US-79.4: a network/stale-chunk failure gets one guarded reload before
    // the jam screen — after a deploy, the reload IS the fix.
    reloadOnceForNetworkError(error);
  }, [error]);

  // Next 16.2 prefers unstable_retry (re-fetches, not just re-renders).
  const retry = unstable_retry ?? reset;

  return (
    <TroubleScreen
      icon={Cog}
      headline="Something jammed on the line."
      body="An unexpected error stopped this page. Trying again usually clears it."
      footnote={error.digest ? `Reference: ${error.digest}` : undefined}
      actions={
        <>
          {retry && <Button onClick={() => retry()}>Try again</Button>}
          <Button variant="outline" render={<Link href="/workbench" />}>
            Back to Things to Do
          </Button>
        </>
      }
      className="min-h-full"
    />
  );
}
