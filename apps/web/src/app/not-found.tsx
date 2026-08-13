import Link from "next/link";
import { Button } from "@/components/ui/button";
import { GoBackButton } from "@/components/go-back-button";
import { TroubleScreen } from "@/components/trouble-screen";

// Root 404: catches every unmatched URL app-wide, plus notFound() from pages
// outside (app) (terminal, files). Must render signed-out, so no auth data.
export default function NotFound() {
  return (
    <TroubleScreen
      showLogo
      code="404"
      headline="This page never made it off the line."
      body="The address may be mistyped, or the page it pointed to may have been moved or deleted."
      actions={
        <>
          <Button render={<Link href="/" />}>Back to Build Mill</Button>
          <GoBackButton />
        </>
      }
      className="min-h-0 flex-1 overflow-y-auto bg-muted/40"
    />
  );
}
