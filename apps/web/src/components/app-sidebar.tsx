"use client";

import { useEffect, useState } from "react";
import Image from "next/image";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  ChevronRight,
  PanelLeftClose,
  PanelLeftOpen,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { ThemeToggle } from "@/components/theme-toggle";
import { UserMenu } from "@/components/user-menu";
import { OrgSwitcher } from "@/components/org-switcher";
import { NotificationBell, type NotificationRow } from "@/components/notification-bell";
import { ADMIN_ENTRIES, NAV_ITEMS, type NavEntry } from "@/components/nav-items";
import { EnvBadge } from "@/components/env-badge";
import { envLogoTint } from "@/lib/env-label";
import type { OrgOption } from "@/lib/active-org";

// US-1.23: collapse state is a browser preference, not an account one.
const COLLAPSE_KEY = "sf-sidebar-collapsed";

export function AppSidebar({
  isSuperadmin,
  email,
  displayName,
  avatarUrl,
  badgeCount = 0,
  orgs = [],
  activeOrgId = null,
  principalId = "",
  notifications = [],
}: {
  isSuperadmin: boolean;
  email: string;
  displayName?: string | null;
  avatarUrl?: string | null;
  /** US-6.1: pending-decision count shown on the Things to Do entry. */
  badgeCount?: number;
  /** US-9.7: the user's orgs + active selection for the switcher. */
  orgs?: OrgOption[];
  activeOrgId?: string | null;
  /** US-9.12: the caller's principal + notifications for the shell bell. */
  principalId?: string;
  notifications?: NotificationRow[];
}) {
  const pathname = usePathname();
  const [collapsed, setCollapsed] = useState(false);
  // Unset = follow the route (open while one of the submenu's pages is
  // active); a manual toggle overrides until the next navigation.
  const [settingsOpen, setSettingsOpen] = useState<Record<
    string,
    boolean
  > | null>(null);

  useEffect(() => {
    setCollapsed(localStorage.getItem(COLLAPSE_KEY) === "1");
  }, []);

  useEffect(() => {
    setSettingsOpen(null);
  }, [pathname]);

  function toggle() {
    setCollapsed((v) => {
      localStorage.setItem(COLLAPSE_KEY, v ? "0" : "1");
      return !v;
    });
  }

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
    <aside
      className={cn(
        "hidden shrink-0 flex-col border-r bg-sidebar transition-[width] md:flex",
        collapsed ? "w-14" : "w-56"
      )}
    >
      <div
        className={cn(
          "flex h-14 items-center gap-2 border-b",
          collapsed ? "justify-center px-0" : "px-4"
        )}
      >
        {!collapsed && (
          <>
            <Image
              src="/buildmill-icon.png"
              alt=""
              width={28}
              height={28}
              priority
              className={cn("size-7 object-contain", envLogoTint())}
            />
            <span className="flex-1 overflow-hidden">
              <Image
                src="/buildmill-wordmark.png"
                alt="Build Mill"
                width={620}
                height={98}
                priority
                className="h-4 w-auto dark:hue-rotate-180 dark:invert"
              />
            </span>
            <EnvBadge />
          </>
        )}
        <Button
          variant="ghost"
          size="icon-sm"
          onClick={toggle}
          title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        >
          {collapsed ? (
            <PanelLeftOpen className="size-4" />
          ) : (
            <PanelLeftClose className="size-4" />
          )}
        </Button>
      </div>
      {orgs.length > 1 && (
        <div className={cn("border-b", collapsed ? "p-2" : "p-3")}>
          <OrgSwitcher orgs={orgs} activeOrgId={activeOrgId} collapsed={collapsed} />
        </div>
      )}
      <nav
        className={cn(
          "flex flex-1 flex-col gap-1 overflow-y-auto",
          collapsed ? "items-center p-2" : "p-3"
        )}
      >
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
            return collapsed ? null : (
              <div
                key={`head-${idx}`}
                className="px-3 pb-1 pt-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground/70"
              >
                {entry.heading}
              </div>
            );
          }
          const { href, label, icon: Icon, children } = entry;
          // US-91.10: a parent is active when the current route is any of its
          // children. The old `startsWith(href)` worked only because the one
          // parent was `/admin`, which prefixed everything under it; with
          // parents anchored at `/admin/orgs` etc., `/admin/users` would
          // leave Accounts unhighlighted.
          const active =
            pathname.startsWith(href) ||
            (children ?? []).some(
              (c) => "href" in c && pathname.startsWith(c.href)
            );

          // Expanded sidebar: an item with children is a disclosure row;
          // its sections carry the active highlight while open.
          if (children && !collapsed) {
            // Per-item disclosure: Settings and Admin open independently.
            const open = settingsOpen?.[href] ?? active;
            return (
              <div key={href} className="flex flex-col gap-1">
                <button
                  type="button"
                  onClick={() =>
                    setSettingsOpen((prev) => ({ ...prev, [href]: !open }))
                  }
                  aria-expanded={open}
                  className={cn(
                    "flex items-center gap-2.5 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                    active && !open
                      ? "bg-sidebar-accent text-sidebar-accent-foreground"
                      : "text-muted-foreground hover:bg-sidebar-accent/60 hover:text-sidebar-accent-foreground"
                  )}
                >
                  <Icon className="size-4 shrink-0" />
                  <span className="flex-1 text-left">{label}</span>
                  <ChevronRight
                    className={cn(
                      "size-4 shrink-0 transition-transform",
                      open && "rotate-90"
                    )}
                  />
                </button>
                {open &&
                  children.map((child, i) =>
                    "heading" in child ? (
                      <div
                        key={`${href}-h-${i}`}
                        className={cn(
                          "px-3 pb-0.5 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground/60",
                          i === 0 ? "pt-1" : "pt-2.5"
                        )}
                      >
                        {child.heading}
                      </div>
                    ) : (
                      <Link
                        key={child.href}
                        href={child.href}
                        className={cn(
                          "rounded-md py-1.5 pl-[2.375rem] pr-3 text-sm font-medium transition-colors",
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

          const showBadge = href === "/workbench" && badgeCount > 0;
          return (
            <Link
              key={href}
              href={href}
              title={collapsed ? label : undefined}
              className={cn(
                "relative flex items-center rounded-md text-sm font-medium transition-colors",
                collapsed ? "justify-center p-2" : "gap-2.5 px-3 py-2",
                active
                  ? "bg-sidebar-accent text-sidebar-accent-foreground"
                  : "text-muted-foreground hover:bg-sidebar-accent/60 hover:text-sidebar-accent-foreground"
              )}
            >
              <Icon className="size-4 shrink-0" />
              {!collapsed && <span className="flex-1">{label}</span>}
              {showBadge &&
                (collapsed ? (
                  <span
                    className="absolute right-1 top-1 size-2 rounded-full bg-primary ring-2 ring-sidebar"
                    aria-label={`${badgeCount} waiting on you`}
                  />
                ) : (
                  <span
                    className="ml-auto min-w-5 rounded-full bg-primary px-1.5 py-0.5 text-center text-[10px] font-semibold leading-none text-primary-foreground tabular-nums"
                    aria-label={`${badgeCount} waiting on you`}
                  >
                    {badgeCount}
                  </span>
                ))}
            </Link>
          );
        })}
      </nav>
      <div
        className={cn(
          "border-t",
          collapsed
            ? "flex flex-col items-center gap-2 p-2"
            : "flex items-center justify-between gap-2 p-3"
        )}
      >
        <div className={cn("flex min-w-0 flex-1 items-center gap-2")}>
          <UserMenu
            email={email}
            displayName={displayName}
            avatarUrl={avatarUrl}
            label={collapsed ? undefined : displayName || email}
            side="top"
            align="start"
          />
        </div>
        <div className="flex shrink-0 items-center gap-1">
          {principalId && activeOrgId && (
            <NotificationBell
              principalId={principalId}
              orgId={activeOrgId}
              initial={notifications}
            />
          )}
          <ThemeToggle />
        </div>
      </div>
    </aside>
  );
}
