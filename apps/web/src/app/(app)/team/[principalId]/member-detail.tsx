"use client";

// US-53.3: a member's detail is a page, not a drawer. This is the body of the
// Team roster's PrincipalDrawer relocated verbatim — same queries, same RPCs,
// same confirm wording — with the slide-over shell dropped. The one mechanical
// difference: the idle reason is fetched here (the drawer received it from
// TeamView's state, which a standalone page does not have).

import { useEffect, useState } from "react";
import Link from "next/link";
import { Bot, Loader2 } from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import { confirmDialog } from "@/components/ui/confirm-dialog";
import { ROLE_LABELS } from "@/lib/permissions";
import { money } from "@/lib/budget";
import { compactTokens, formatWorkSeconds } from "@/lib/work-seconds";
import { ProjectAccess } from "../capabilities";
import { RemoveMember } from "../remove-member";
import {
  formatWhen,
  principalName,
  type AgentEffort,
  type AgentSeat,
  type MemberRow,
  type WorkerRow,
} from "../team-view";

export function MemberDetail({
  orgId,
  member,
  workers,
  projects,
  slot,
  embedded,
  moduleLabel,
  tokenCount,
  effort,
  effortWindowDays,
  canManage,
  onRemoved,
  onSuspendToggled,
}: {
  orgId: string;
  member: MemberRow;
  workers: WorkerRow[];
  projects: { id: string; name: string }[];
  /** US-27.9: the machine this agent runs on, when Build Mill owns it. The
   *  row already names it ("Pod-001 slot 5"), but that text isn't a link —
   *  the whole row is itself a toggle button, and a link can't nest inside
   *  one. This is that link, restored here instead. */
  slot?: AgentSeat | null;
  /** True when nested inline in a Team row's expand panel — the row already
   *  shows the name/kind, status, and current task, so this suppresses the
   *  repeated header and the fields the row already surfaces. */
  embedded?: boolean;
  /** us-109.1: the four facts the roster row stopped showing, because a
   *  manager looks them up rather than scans them. `null`/absent renders
   *  nothing rather than a dash — a person has no module, and an agent with
   *  no runs in the window has no effort. */
  moduleLabel?: string | null;
  tokenCount?: number;
  effort?: AgentEffort | null;
  effortWindowDays?: number;
  /** Gates the Remove action below (`manage_members`). An agent's Remove is
   *  on its settings page instead — this is the only home a person has. */
  canManage?: boolean;
  onRemoved?: () => void;
  /** us-116.5: flips the agent's membership between active and suspended —
   *  the caller owns the write (the roster's `mutate`). Absent = no control. */
  onSuspendToggled?: () => Promise<void> | void;
}) {
  const isAgent = member.principals?.kind === "agent";
  const [caps, setCaps] = useState<{ project_id: string }[] | null>(null);

  // Project access applies to any worker, human or agent — it's the
  // worker_capabilities row gitproxy.py's clone/fetch gate checks
  // (US-3.12/31.3/55.1). A human's own worker (their "Access token" row)
  // needs it exactly like an agent's does.
  const primaryWorker = workers[0] ?? null;

  useEffect(() => {
    const supabase = createClient();
    let cancelled = false;
    async function load() {
      // Project access keyed to the member's own worker. US-55.1: any row on
      // the pair means access — the per-kind matrix is gone.
      if (primaryWorker) {
        const { data } = await supabase
          .from("worker_capabilities")
          .select("project_id")
          .eq("worker_id", primaryWorker.id)
          .order("created_at", { ascending: true });
        if (!cancelled) setCaps(data ?? []);
      } else {
        setCaps([]);
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, [member.principal_id, workers, orgId]);

  return (
    <div className="flex w-full max-w-3xl flex-col gap-6">
      {!embedded && (
        <div className="min-w-0">
          <div className="flex items-center gap-1.5 text-xl font-semibold">
            {isAgent && <Bot className="size-5 text-muted-foreground" />}
            <span className="truncate">{principalName(member)}</span>
          </div>
          <p className="truncate text-xs text-muted-foreground">
            {isAgent ? "Agent" : "Person"}
            {member.principals?.email ? ` · ${member.principals.email}` : ""}
            {/* US-61.1: role is fixed and non-editable for agents — showing
                it a second time next to the "Agent" kind above would just
                repeat it. */}
            {!isAgent && ` · ${ROLE_LABELS[member.role] ?? member.role}`}
          </p>
        </div>
      )}

      {/* us-109.1: what the roster row used to carry — the CLI module, the
          token count, the join date. Fixed facts, read once, so they belong
          one click in rather than on a line that is scanned. */}
      <section className="grid gap-2">
        <h3 className="text-sm font-semibold">About</h3>
        <dl className="grid gap-x-8 gap-y-1.5 text-xs sm:grid-cols-3">
          {isAgent && (
            <div>
              <dt className="text-muted-foreground">Agent type</dt>
              <dd className="font-medium">{moduleLabel ?? "Not set"}</dd>
            </div>
          )}
          {tokenCount !== undefined && (
            <div>
              <dt className="text-muted-foreground">Tokens</dt>
              <dd className="font-medium">
                {tokenCount} active token{tokenCount === 1 ? "" : "s"}
              </dd>
            </div>
          )}
          <div>
            <dt className="text-muted-foreground">Joined</dt>
            <dd className="font-medium">{formatWhen(member.created_at)}</dd>
          </div>
        </dl>
      </section>

      {/* us-109.1: the output half of US-91.12's effort line. The row keeps
          "worked · completed" — whether this agent is earning its seat — and
          the rest is here, where there is room to say which window it covers
          rather than showing five unlabelled numbers. */}
      {isAgent && effort && (
        <section className="grid gap-2">
          <h3 className="text-sm font-semibold">
            Output{effortWindowDays ? ` (last ${effortWindowDays} days)` : ""}
          </h3>
          <dl className="grid gap-x-8 gap-y-1.5 text-xs sm:grid-cols-3">
            <div>
              <dt className="text-muted-foreground">Worked</dt>
              <dd className="font-medium tabular-nums">
                {formatWorkSeconds(effort.workSeconds)} · {effort.issuesCompleted}{" "}
                completed
              </dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Lines of code</dt>
              <dd className="font-medium tabular-nums">
                +{effort.linesAdded.toLocaleString()} −
                {effort.linesRemoved.toLocaleString()}
              </dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Tokens &amp; cost</dt>
              <dd className="font-medium tabular-nums">
                {compactTokens(effort.tokens)} · {money(effort.costUsd)}
              </dd>
            </div>
          </dl>
        </section>
      )}

      {/* US-27.9: the row already names the machine ("Pod-001 slot 5") but
          that text can't be a link — the row is itself a toggle button, and
          links can't nest inside one. This is that link, restored here. */}
      {isAgent && slot?.serverId && (
        <Link
          href={`/servers/${slot.serverId}`}
          className="flex w-fit items-center gap-1 text-xs text-muted-foreground hover:text-foreground hover:underline"
        >
          View machine ({slot.hostName}, slot {slot.slotIndex})
          <span aria-hidden>→</span>
        </Link>
      )}

      {/* Project access — US-55.1: which projects, not a matrix. Applies to
          any worker: a human's own token needs a project's access row to
          git-clone/fetch through the factory remote exactly like an agent's
          does (gitproxy.py's check_capabilities gate, US-3.12/31.3). */}
      {primaryWorker && (
        <section className="grid gap-2">
          <h3 className="text-sm font-semibold">Project access</h3>
          {caps === null ? (
            <Loader2 className="size-4 animate-spin text-muted-foreground" />
          ) : (
            <ProjectAccess
              workerId={primaryWorker.id}
              orgId={orgId}
              principalId={member.principal_id}
              projects={projects}
              rows={caps}
              isAgent={isAgent}
            />
          )}
        </section>
      )}

      {/* us-116.5: an agent's Suspend left the roster row — there it wore the
          same ▶/⏸ as a pause and REVOKED the token (migration 089's cascade;
          it killed the Sandy fleet on 2026-08-09). It lives here, worded as
          what it does, behind a confirm. Reactivate restores exactly the
          tokens suspension revoked (us-55.2). */}
      {isAgent && canManage && onSuspendToggled && (
        <section className="grid gap-2 border-t pt-4">
          <h3 className="text-sm font-semibold">
            {member.status === "suspended" ? "Reactivate this agent" : "Suspend this agent"}
          </h3>
          <p className="text-xs text-muted-foreground">
            {member.status === "suspended"
              ? "Restores the worker token suspension revoked; the machine's service reconnects on its own."
              : "Suspend revokes this agent's worker token — its service keeps running and can claim nothing. To pause work without revoking anything, use Stop on the roster instead."}
          </p>
          <SuspendAgentButton
            suspended={member.status === "suspended"}
            name={principalName(member)}
            onConfirmed={onSuspendToggled}
          />
        </section>
      )}

      {/* us-109.1: a person has no settings page, so this is where their
          Remove lives now that it is off the roster row. An agent's is on
          `/team/{id}/settings` — deliberately not repeated here, so there is
          exactly one place to remove any given member. */}
      {!isAgent && canManage && onRemoved && (
        <section className="grid gap-2 border-t pt-4">
          <h3 className="text-sm font-semibold">Remove from this org</h3>
          <p className="text-xs text-muted-foreground">
            They lose access immediately and their tokens are revoked. Suspend
            instead if they may come back.
          </p>
          <RemoveMember
            orgId={orgId}
            principalId={member.principal_id}
            name={principalName(member)}
            isAgent={false}
            onRemoved={onRemoved}
            className="mt-1"
          />
        </section>
      )}
    </div>
  );
}


/** us-116.5: the confirm that says what suspending an agent does. */
function SuspendAgentButton({
  suspended,
  name,
  onConfirmed,
}: {
  suspended: boolean;
  name: string;
  onConfirmed: () => Promise<void> | void;
}) {
  const [busy, setBusy] = useState(false);
  return (
    <button
      type="button"
      disabled={busy}
      data-testid={suspended ? "agent-reactivate" : "agent-suspend"}
      className="w-fit rounded-md border px-2.5 py-1 text-xs hover:bg-muted disabled:opacity-50"
      onClick={async () => {
        const ok = await confirmDialog({
          title: suspended ? `Reactivate ${name}?` : `Suspend ${name}?`,
          description: suspended
            ? "Its worker token is restored and the machine's service reconnects on its own."
            : `This revokes ${name}'s worker token. Its service keeps running and can claim nothing until you reactivate it. To pause work without revoking anything, use Stop on the roster instead.`,
          confirmLabel: suspended ? "Reactivate" : "Suspend — revoke its token",
        });
        if (!ok) return;
        setBusy(true);
        try {
          await onConfirmed();
        } finally {
          setBusy(false);
        }
      }}
    >
      {suspended ? "Reactivate" : "Suspend"}
    </button>
  );
}
