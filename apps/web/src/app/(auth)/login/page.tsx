import Image from "next/image";
import { cn } from "@/lib/utils";
import { EnvBadge } from "@/components/env-badge";
import { envLogoTint } from "@/lib/env-label";
import { LoginForm } from "./login-form";

export default function LoginPage() {
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
        <EnvBadge className="px-1.5 text-xs" />
        <p className="text-xs text-muted-foreground">
          AI delivery pipeline, human in the loop
        </p>
      </div>
      <LoginForm />
    </div>
  );
}
