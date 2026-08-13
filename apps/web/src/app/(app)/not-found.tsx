import Link from "next/link";
import { Button } from "@/components/ui/button";
import { GoBackButton } from "@/components/go-back-button";
import { TroubleScreen } from "@/components/trouble-screen";

// In-shell 404: renders inside the (app) layout (sidebar intact) for the
// notFound() calls in project/work-item/worker/etc. pages.
export default function AppNotFound() {
  return (
    <TroubleScreen
      code="404"
      headline="This page never made it off the line."
      body="Whatever lived at this address may have been deleted, or the link is stale."
      actions={
        <>
          <Button render={<Link href="/dashboard" />}>
            Back to Things to Do
          </Button>
          <GoBackButton />
        </>
      }
      className="min-h-full"
    />
  );
}
