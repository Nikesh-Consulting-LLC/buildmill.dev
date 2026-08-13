"use client";

// US-53.1: the answer to "will a run dispatched now actually bill the
// subscription?" — the four-table investigation of 2026-07-30, as product.
// Green if and only if a run would bill it; each red names its fix and links
// to where the fix lives. Renders nothing in API mode.
//
// US-53.2 moved it here from the settings page so the add-agent wizard can
// render the same checks inline — one implementation, two surfaces.

import { useEffect, useState, type ReactNode } from "react";
import Link from "next/link";

import { createClient } from "@/lib/supabase/client";

export function BillingReadiness({
  billing,
  online,
  declaresAuth,
  workerId,
  machineConnectedAt,
  pending = false,
}: {
  billing: string;
  online: boolean;
  declaresAuth: boolean;
  /** Null before the agent exists (the wizard's billing step): the slot
   *  lookup is skipped and `machineConnectedAt` stands in for it. */
  workerId: string | null;
  /** US-53.2: the chosen machine's connected-account marker, for readiness
   *  before any slot row ties the worker to the machine. */
  machineConnectedAt?: string | null;
  /** US-53.2: pre-creation the runner cannot have said hello yet, so the
   *  runner-support check reads as "confirmed at first hello" rather than
   *  a false alarm about an agent that does not exist. Warns, never blocks. */
  pending?: boolean;
}) {
  const [orgToken, setOrgToken] = useState<string | null>(null);
  const [machineConnected, setMachineConnected] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    if (billing !== "subscription") return;
    let cancelled = false;
    (async () => {
      const supabase = createClient();
      // RLS scopes both reads to the member's org.
      const sub = await supabase
        .from("claude_subscriptions")
        .select("key_last4")
        .limit(1)
        .maybeSingle();
      let connected: string | null = machineConnectedAt ?? null;
      if (workerId) {
        const slot = await supabase
          .from("agent_slots")
          .select("agent_servers(claude_connected_at)")
          .eq("worker_id", workerId)
          .limit(1)
          .maybeSingle();
        const server = slot.data?.agent_servers as
          | { claude_connected_at: string | null }
          | { claude_connected_at: string | null }[]
          | null
          | undefined;
        const s = Array.isArray(server) ? server[0] : server;
        connected = s?.claude_connected_at ?? connected;
      }
      if (cancelled) return;
      setOrgToken(sub.data?.key_last4 ?? null);
      setMachineConnected(connected);
      setLoaded(true);
    })();
    return () => {
      cancelled = true;
    };
  }, [billing, workerId, machineConnectedAt]);

  if (billing !== "subscription") return null;
  if (!loaded)
    return (
      <p className="mt-2 text-xs text-muted-foreground">Checking readiness…</p>
    );

  const checks: { ok: boolean; node: ReactNode }[] = [
    orgToken
      ? { ok: true, node: <>Credential: org token · …{orgToken}</> }
      : machineConnected
        ? {
            ok: true,
            node: (
              <>
                Credential: this machine&apos;s connected Claude account (
                {new Date(machineConnected).toLocaleDateString()})
              </>
            ),
          }
        : {
            ok: false,
            node: (
              <>
                No credential reachable —{" "}
                <Link href="/settings/llm-providers" className="underline">
                  paste a token
                </Link>{" "}
                or connect the{" "}
                <Link href="/servers" className="underline">
                  machine&apos;s own account
                </Link>
                . (A login the machine holds privately still works, but the
                factory cannot see it.)
              </>
            ),
          },
    pending
      ? {
          ok: true,
          node: (
            <>
              Runner support is confirmed at the agent&apos;s first hello — a
              fresh supervisor honors subscription billing.
            </>
          ),
        }
      : !online
        ? {
            ok: false,
            node: (
              <>Runner is not connected — nothing can claim work right now.</>
            ),
          }
        : declaresAuth
          ? { ok: true, node: <>Runner honors subscription billing.</> }
          : {
              ok: false,
              node: (
                <>
                  Runner must be updated — its supervisor predates subscription
                  billing and will keep minting metered keys.
                </>
              ),
            },
  ];
  const allGreen = checks.every((c) => c.ok);

  return (
    <div className="mt-2 grid gap-1 text-xs">
      {/* "Ready" is a claim about a dispatch happening NOW, which pre-creation
          cannot honestly make — the wizard's Done step earns it instead. */}
      {allGreen && !pending && (
        <p className="font-medium text-emerald-600 dark:text-emerald-400">
          Ready — a run dispatched now bills the subscription.
        </p>
      )}
      {checks.map((c, i) => (
        <p
          key={i}
          className={
            c.ok
              ? "text-muted-foreground"
              : "text-amber-700 dark:text-amber-400"
          }
        >
          {c.ok ? "✓" : "✗"} {c.node}
        </p>
      ))}
    </div>
  );
}
