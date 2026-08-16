import { Bug, ClipboardList, Code, Rocket, type LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import {
  AGENT_ROLES,
  roleOfKind,
  rolesForKinds,
  type AgentRoleKey,
} from "@/lib/agent-roles";

/**
 * us-107.3: one glyph per agent capability, everywhere.
 *
 * The four roles (US-77.1) already are the vocabulary the manager works in —
 * `AGENT_ROLES`, the four checkboxes on an agent's settings page. What they
 * never had was a *picture*, so "what can this agent do" was a sentence you had
 * to open a page to read, and "what will this button do" was a rocket
 * regardless of whether it dispatched planning, code or a deploy.
 *
 * | role | icon | why |
 * |---|---|---|
 * | planning | `ClipboardList` | a plan is a checklist you write before building |
 * | programming | `Code` | the literal thing |
 * | testing | `Bug` | what a test run is hunting |
 * | deployment | `Rocket` | ship it |
 *
 * The icons live here rather than on `AGENT_ROLES` deliberately:
 * `agent-roles.ts` is pure and unit-tested by the node runner, which cannot
 * parse JSX and should not be resolving `lucide-react` to check a mapping.
 */

export const ROLE_ICONS: Record<AgentRoleKey, LucideIcon> = {
  planning: ClipboardList,
  programming: Code,
  testing: Bug,
  deployment: Rocket,
};

/** The icon for a *run kind* — `plan`, `code`, `deploy`, `guidelines`… —
 *  resolved through the role that owns it, so a kind can never grow a glyph
 *  that disagrees with its role's. Null for a kind no role claims. */
export function iconForKind(kind: string): LucideIcon | null {
  const role = roleOfKind(kind);
  return role ? ROLE_ICONS[role.key] : null;
}

/** One capability, at a size that fits a row. */
export function RoleIcon({
  role,
  className,
}: {
  role: AgentRoleKey;
  className?: string;
}) {
  const Icon = ROLE_ICONS[role];
  return <Icon className={cn("size-4", className)} />;
}

/**
 * All four capabilities, with the ones this agent does not have greyed out.
 *
 * Always four, never a filtered list: the manager's complaint was having to
 * open an agent to find out what it does, and a row showing only what is
 * present answers "what can it do" without answering "what can it not do".
 * Absence is the information you are usually looking for.
 */
export function RoleCapabilities({
  kinds,
  className,
  iconClassName,
}: {
  /** `runner_config.enabled_kinds`. `null`/`undefined` means every role — the
   *  us-53.4 rule that a never-saved agent is unrestricted, not benched. */
  kinds: readonly string[] | null | undefined;
  className?: string;
  iconClassName?: string;
}) {
  const have = new Set(rolesForKinds(kinds));
  return (
    <span className={cn("inline-flex items-center gap-1", className)}>
      {AGENT_ROLES.map((role) => {
        const Icon = ROLE_ICONS[role.key];
        const on = have.has(role.key);
        return (
          // The span carries the tooltip and the label: an <svg> takes neither
          // a `title` attribute nor a useful accessible name on its own, so
          // hanging them here is what makes the row readable to a screen
          // reader and hoverable with a mouse.
          <span
            key={role.key}
            title={on ? role.label : `${role.label} — not enabled`}
            aria-label={`${role.label}: ${on ? "enabled" : "not enabled"}`}
            className="inline-flex"
          >
            <Icon
              aria-hidden
              className={cn(
                "size-4 shrink-0",
                on
                  ? "text-foreground"
                  : // Greyed, not hidden. Still legible enough to read as an
                    // absence rather than a rendering glitch.
                    "text-muted-foreground/30",
                iconClassName,
              )}
            />
          </span>
        );
      })}
    </span>
  );
}
