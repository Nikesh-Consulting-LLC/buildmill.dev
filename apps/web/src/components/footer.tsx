import { appVersion } from "@/lib/app-version";

export function Footer() {
  return (
    <footer className="border-t px-4 py-3 text-center text-xs text-muted-foreground md:px-6">
      © 2026 Nikesh Consulting LLC. (Build: {appVersion()})
    </footer>
  );
}
