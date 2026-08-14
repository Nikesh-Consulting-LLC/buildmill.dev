import Image from "next/image";
import { redirect } from "next/navigation";
import { cn } from "@/lib/utils";
import { envLogoTint } from "@/lib/env-label";
import { createClient } from "@/lib/supabase/server";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { GateSignOut } from "./gate-sign-out";

// us-94.1: the beta gate. A new signup can authenticate, but the (app)-layout
// gate sends it here until a platform admin approves the account from
// SuperAdmin → Accounts → Users. Lives outside the (app) group so the
// redirect can never loop; an approved user who lands here is bounced to the
// dashboard, so the page is only ever seen by the people it's for.
export default async function GatePage() {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/login");

  const { data: profile } = await supabase
    .from("profiles")
    .select("approved_at")
    .eq("id", user.id)
    .maybeSingle();
  if (profile?.approved_at) redirect("/workbench");

  return (
    <div className="flex min-h-0 flex-1 flex-col items-center justify-center-safe gap-6 overflow-y-auto bg-muted/40 p-4">
      <div className="flex flex-col items-center gap-2">
        {/* The lockup keeps its own light background so it stays legible in dark mode. */}
        <div className="rounded-2xl bg-[#f6f6f6] px-4 py-3">
          <Image
            src="/buildmill-logo.png"
            alt="Build Mill logo"
            width={620}
            height={446}
            priority
            className={cn("h-28 w-auto object-contain", envLogoTint())}
          />
        </div>
      </div>
      <Card className="w-full max-w-md">
        <CardHeader>
          <CardTitle>You&apos;re in the queue</CardTitle>
          <CardDescription>
            Build Mill is in <span className="font-medium">beta</span>.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-3 text-sm text-muted-foreground">
          <p>
            Due to capacity, every account goes through approval before it
            opens — a human looks at each one, and yours is in the queue.
          </p>
          <p>
            There&apos;s nothing you need to do. Sign back in later; the moment
            your account is approved, this page gets out of your way.
          </p>
          <p className="text-xs">
            Signed in as <span className="font-medium text-foreground">{user.email}</span>
          </p>
        </CardContent>
        <CardFooter className="mt-2">
          <GateSignOut />
        </CardFooter>
      </Card>
    </div>
  );
}
