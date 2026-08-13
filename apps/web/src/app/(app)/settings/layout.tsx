import { ApiStatus } from "./api-status";

// US-2.24: shared settings shell — heading and backend-status strip
// (visible on every subpage). Section navigation lives in the main
// nav as a Settings submenu (sidebar + mobile drawer).
export default function SettingsLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="flex w-full flex-col gap-6">
      <div className="flex flex-wrap items-end justify-between gap-x-6 gap-y-2">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Settings</h1>
          <p className="text-sm text-muted-foreground">
            Global configuration for the factory.
          </p>
        </div>
        <ApiStatus />
      </div>
      <div className="min-w-0">{children}</div>
    </div>
  );
}
