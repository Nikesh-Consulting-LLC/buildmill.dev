import { appVersion, versionDetail } from "@/lib/app-version";

export function Footer() {
  return (
    <footer className="border-t px-4 py-3 text-center text-xs text-muted-foreground md:px-6">
      © 2026 Nikesh Consulting LLC. (
      {/* US-91.16: the compact line answers "which build, and when"; the full
          sha, branch and exact timestamp are one hover away. */}
      <span title={versionDetail()}>Build: {appVersion()}</span>)
    </footer>
  );
}
