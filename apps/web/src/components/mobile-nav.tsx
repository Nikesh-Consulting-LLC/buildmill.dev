"use client";

// US-2.12: below `md` the sidebar is hidden with no replacement — this
// header + drawer is the mobile navigation. Same entries and active
// highlighting as the sidebar; user menu and theme toggle live in the
// drawer footer. At `md`+ this renders nothing.

import { useEffect, useState } from "react";
import Image from "next/image";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { ChevronRight, Menu, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { ThemeToggle } from "@/components/theme-toggle";
import { UserMenu } from "@/components/user-menu";
import { ADMIN_ENTRIES, NAV_ITEMS, type NavEntry } from "@/components/nav-items";
import { EnvBadge } from "@/components/env-badge";
import { OrgSwitcher } from "@/components/org-switcher";
import type { OrgOption } from "@/lib/active-org";
import { envLogoTint } from "@/lib/env-label";

export function MobileNav({
  isSuperadmin,
  email,
  displayName,
  avatarUrl,
  badgeCount = 0,
  orgs = [],
  activeOrgId = null,
}: {
  isSuperadmin: boolean;
  email: string;
  displayName?: string | null;
  avatarUrl?: string | null;
  /** US-6.1: pending-decision count shown on the Things to Do entry. */
  badgeCount?: number;
  /** UAT: the workspace picker lived only in the sidebar, which is
   *  `hidden md:flex` — so on a phone there was no way to switch at all. */
  orgs?: OrgOption[];
  activeOrgId?: string | null;
}) {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  // null = follow the route (open while a settings page is active);
  // a manual toggle overrides until the next navigation.
  const [settingsOpen, setSettingsOpen] = useState<Record<
    string,
    boolean
  > | null>(null);

  // navigating closes the drawer
  useEffect(() => {
    setOpen(false);
    setSettingsOpen(null);
  }, [pathname]);

  const items: NavEntry[] = isSuperadmin
    ? [
        ...NAV_ITEMS,
        { separator: true },
        // US-91.10: four menus under a section heading, not one drawer of
        // fifteen links. `/admin` itself keeps working by URL; it no longer
        // owns a row.
        ...ADMIN_ENTRIES,
      ]
    : NAV_ITEMS;

  return (
    <div className="md:hidden">
      <header className="flex h-14 items-center justify-between border-b bg-sidebar px-3">
        <span className="flex items-center gap-2">
          <Image
            src="/buildmill-icon.png"
            alt=""
            width={24}
            height={24}
            className={cn("size-6 object-contain", envLogoTint())}
          />
          <Image
            src="/buildmill-wordmark.png"
            alt="Build Mill"
            width={620}
            height={98}
            className="h-4 w-auto dark:hue-rotate-180 dark:invert"
          />
          <EnvBadge />
        </span>
        <Button
          variant="ghost"
          size="icon"
          className="relative"
          aria-label={
            badgeCount > 0
              ? `Open navigation — ${badgeCount} waiting on you`
              : "Open navigation"
          }
          onClick={() => setOpen(true)}
        >
          <Menu className="size-5" />
          {badgeCount > 0 && (
            <span className="absolute right-1.5 top-1.5 size-2 rounded-full bg-primary ring-2 ring-sidebar" />
          )}
        </Button>
      </header>

      {open && (
        <div className="fixed inset-0 z-50">
          <div
            className="absolute inset-0 bg-black/50"
            onClick={() => setOpen(false)}
            aria-hidden
          />
          <div className="absolute inset-y-0 left-0 flex w-72 max-w-[85vw] flex-col bg-sidebar shadow-xl">
            <div className="flex h-14 items-center justify-between border-b px-4">
              <Image
                src="/buildmill-wordmark.png"
                alt="Build Mill"
                width={620}
                height={98}
                className="h-4 w-auto dark:hue-rotate-180 dark:invert"
              />
              <Button
                variant="ghost"
                size="icon"
                aria-label="Close navigation"
                onClick={() => setOpen(false)}
              >
                <X className="size-5" />
              </Button>
            </div>
            {/* UAT: the workspace picker, which until now existed only in
                the desktop sidebar. First thing in the drawer, because every
                entry below it is scoped to whichever workspace is active. */}
            {orgs.length > 1 && (
              <div className="border-b p-3">
                <OrgSwitcher orgs={orgs} activeOrgId={activeOrgId} />
              </div>
            )}
            <nav className="flex flex-1 flex-col gap-1 overflow-y-auto p-3">
              {items.map((entry, idx) => {
                if ("separator" in entry) {
                  return (
                    <div
                      key={`sep-${idx}`}
                      className="my-1.5 border-t border-border/60"
                    />
                  );
                }
                if ("heading" in entry) {
                  return (
                    <div
                      key={`head-${idx}`}
                      className="px-3 pb-1 pt-3 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground/70"
                    >
                      {entry.heading}
                    </div>
                  );
                }
                const { href, label, icon: Icon, children } = entry;
                // US-91.10: active when the route is the parent or any child.
                const active =
                  pathname.startsWith(href) ||
                  (children ?? []).some(
                    (c) => "href" in c && pathname.startsWith(c.href)
                  );

                // An item with children is a disclosure row; its sections
                // carry the active highlight while open.
                if (children) {
                  const openSubmenu = settingsOpen?.[href] ?? active;
                  return (
                    <div key={href} className="flex flex-col gap-1">
                      <button
                        type="button"
                        onClick={() =>
                          setSettingsOpen((prev) => ({
                            ...prev,
                            [href]: !openSubmenu,
                          }))
                        }
                        aria-expanded={openSubmenu}
                        className={cn(
                          "flex min-h-11 items-center gap-3 rounded-md px-3 text-sm font-medium transition-colors",
                          active && !openSubmenu
                            ? "bg-sidebar-accent text-sidebar-accent-foreground"
                            : "text-muted-foreground hover:bg-sidebar-accent/60 hover:text-sidebar-accent-foreground"
                        )}
                      >
                        <Icon className="size-5" />
                        <span className="flex-1 text-left">{label}</span>
                        <ChevronRight
                          className={cn(
                            "size-5 transition-transform",
                            openSubmenu && "rotate-90"
                          )}
                        />
                      </button>
                      {openSubmenu &&
                        children.map((child, i) =>
                          "heading" in child ? (
                            <div
                              key={`${href}-h-${i}`}
                              className={cn(
                                "px-3 pb-0.5 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground/60",
                                i === 0 ? "pt-1" : "pt-3"
                              )}
                            >
                              {child.heading}
                            </div>
                          ) : (
                            <Link
                              key={child.href}
                              href={child.href}
                              onClick={() => setOpen(false)}
                              className={cn(
                                "flex min-h-11 items-center rounded-md pl-11 pr-3 text-sm font-medium transition-colors",
                                pathname.startsWith(child.href)
                                  ? "bg-sidebar-accent text-sidebar-accent-foreground"
                                  : "text-muted-foreground hover:bg-sidebar-accent/60 hover:text-sidebar-accent-foreground"
                              )}
                            >
                              {child.label}
                            </Link>
                          )
                        )}
                    </div>
                  );
                }

                const showBadge = href === "/dashboard" && badgeCount > 0;
                return (
                  <Link
                    key={href}
                    href={href}
                    onClick={() => setOpen(false)}
                    className={cn(
                      // ≥40px touch targets
                      "flex min-h-11 items-center gap-3 rounded-md px-3 text-sm font-medium transition-colors",
                      active
                        ? "bg-sidebar-accent text-sidebar-accent-foreground"
                        : "text-muted-foreground hover:bg-sidebar-accent/60 hover:text-sidebar-accent-foreground"
                    )}
                  >
                    <Icon className="size-5" />
                    <span className="flex-1">{label}</span>
                    {showBadge && (
                      <span
                        className="min-w-5 rounded-full bg-primary px-1.5 py-0.5 text-center text-[11px] font-semibold leading-none text-primary-foreground tabular-nums"
                        aria-label={`${badgeCount} waiting on you`}
                      >
                        {badgeCount}
                      </span>
                    )}
                  </Link>
                );
              })}
            </nav>
            <div className="flex items-center justify-between gap-2 border-t p-3">
              <div className="flex min-w-0 items-center gap-2">
                <UserMenu
                  email={email}
                  displayName={displayName}
                  avatarUrl={avatarUrl}
                  side="top"
                  align="start"
                />
                <span className="truncate text-xs font-medium text-muted-foreground">
                  {displayName || email}
                </span>
              </div>
              <ThemeToggle />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
