"use client";

// US-32.1: the agent pages are siblings, so each says the other exists.
// Overview is gone as a page — its content is now the Team row's expand
// panel — so these two are the only sibling views left.

import Link from "next/link";

export function AgentTabs({
  principalId,
  active,
}: {
  principalId: string;
  active: "console" | "settings";
}) {
  const tabs = [
    { key: "console", label: "Console", href: `/team/${principalId}/runner` },
    { key: "settings", label: "Settings", href: `/team/${principalId}/settings` },
  ] as const;
  return (
    <nav className="flex gap-1 border-b text-sm" aria-label="Agent views">
      {tabs.map((t) => (
        <Link
          key={t.key}
          href={t.href}
          aria-current={active === t.key ? "page" : undefined}
          className={`-mb-px border-b-2 px-3 py-1.5 ${
            active === t.key
              ? "border-primary font-medium text-foreground"
              : "border-transparent text-muted-foreground hover:text-foreground"
          }`}
        >
          {t.label}
        </Link>
      ))}
    </nav>
  );
}
