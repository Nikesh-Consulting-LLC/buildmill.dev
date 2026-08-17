/**
 * US-78.11: an interactive agent's CLI window, reachable from the roster
 * whether or not it is working.
 *
 * Every other way into the console goes through a run — Activity, a work item,
 * the run page — so an idle agent had no door at all, and the one surface where
 * you look at *an agent* could not show you the agent's own session.
 *
 * The console itself is run-scoped and stays that way (the ACP session belongs
 * to a run). This page resolves which run to show: the one being held now, or
 * the most recent one, or nothing yet. That is the whole difference, and it is
 * why the page is thin.
 */

import { notFound, redirect } from "next/navigation";
import Link from "next/link";
import { ArrowLeft, TerminalSquare } from "lucide-react";

import { EmptyState } from "@/components/empty-state";
import { createClient } from "@/lib/supabase/server";
import { InteractiveConsole } from "../../../runs/[id]/interactive-console";
import { StartSession } from "./start-session";
import { CloseSession } from "./close-session";

// US-78.10: mirrored from the API's own constant so the page can state the
// timeout before it bites (AC3) rather than describing it vaguely.
const IDLE_TIMEOUT_MINUTES = 30;

export default async function AgentConsolePage({
  params,
}: {
  params: Promise<{ principalId: string }>;
}) {
  const { principalId } = await params;
  const supabase = await createClient();

  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/login");

  // The worker behind this principal. RLS scopes it to the caller's org, so a
  // principal from another workspace simply is not found.
  const { data: worker } = await supabase
    .from("workers")
    .select("id, name, org_id")
    .eq("principal_id", principalId)
    .maybeSingle();
  if (!worker) notFound();

  const { data: config } = await supabase
    .from("runner_config")
    .select("enabled_modules")
    .eq("worker_id", worker.id)
    .maybeSingle();
  const isInteractive = (
    (config?.enabled_modules ?? []) as string[]
  ).includes("interactive");

  // Running first, then the most recent — so opening this while the agent works
  // lands on the live session, and opening it later lands on what it last did.
  const { data: runs } = await supabase
    .from("runs")
    .select("id, kind, status, created_at")
    .eq("worker_id", worker.id)
    .order("created_at", { ascending: false })
    .limit(20);
  const running = (runs ?? []).find((r) => r.status === "running");
  const latest = running ?? (runs ?? [])[0] ?? null;

  // US-78.10: a session with no work item outranks both. It is the
  // conversation happening right now, and it is the one the manager came here
  // to talk to.
  const { data: liveSession } = await supabase
    .from("agent_sessions")
    .select("id, status, project_id, created_at")
    .eq("worker_id", worker.id)
    .in("status", ["opening", "open"])
    .order("created_at", { ascending: false })
    .maybeSingle();

  const { data: projects } = isInteractive
    ? await supabase
        .from("projects")
        .select("id, name")
        .eq("org_id", worker.org_id)
        .is("archived_at", null)
        .order("name", { ascending: true })
    : { data: [] };

  // US-88.1: what this window is, in three words for the console's own chrome
  // bar. It used to be a heading and a paragraph above the terminal — which is
  // the wrong trade on a page that is nothing but a terminal, because the
  // agent's name is already in the chrome and the rest is two lines of screen
  // spent saying what the status dot says.
  const label = liveSession
    ? "live session"
    : running
      ? "working now"
      : latest
        ? "last session · read only"
        : "no session";

  return (
    <div className="flex h-full w-full min-h-0 flex-col gap-2">
      <Link
        href="/team"
        className="inline-flex shrink-0 items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="size-3" />
        Team
      </Link>

      {!isInteractive ? (
        <EmptyState
          icon={TerminalSquare}
          title="This agent does not hold a live session"
          description="Only a Buildmill Interactive Agent runs an ACP session you can watch and steer. Every other agent type runs a one-shot command line, so there is nothing to attach to — its work shows on the run page instead."
        />
      ) : liveSession ? (
        <>
          <InteractiveConsole
            runId={liveSession.id as string}
            scope="session"
            title={worker.name as string}
            label={label}
            fill
          />
          <CloseSession sessionId={liveSession.id as string} />
        </>
      ) : (
        <>
          {/* US-78.10: the other way in — no run required. Offered above the
              run history, because a manager who came here to talk to the agent
              wants this, not a transcript of what it did last week. */}
          <StartSession
            workerId={worker.id as string}
            principalId={principalId}
            projects={(projects ?? []) as { id: string; name: string }[]}
            idleTimeoutMinutes={IDLE_TIMEOUT_MINUTES}
          />
          {latest ? (
            <InteractiveConsole
              runId={latest.id as string}
              title={worker.name as string}
              label={label}
              fill
            />
          ) : (
            <EmptyState
              icon={TerminalSquare}
              title="Nothing to show yet"
              description="This agent has not held a run. Start a session above, or dispatch it work — either way its conversation appears here, and the CLI button on the Team roster glows while it is busy."
            />
          )}
        </>
      )}
    </div>
  );
}
