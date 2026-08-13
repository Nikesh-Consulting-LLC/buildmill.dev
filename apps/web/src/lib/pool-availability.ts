/** US-57.10: why the agent-pool option is unavailable, and what to ask for.
 *
 * Three different situations used to arrive at the wizard as the same empty
 * list, because `available_agent_pools()` filtered on `status = 'ready'`. It
 * had one sentence for all three, and on 2026-07-31 it told a manager to
 * "provision or resize" a pool that had 31 of 32 slots free and was sitting
 * at `status = 'error'` — advice that would have produced a second row the
 * same filter rejected.
 *
 * The RPC now reports `status` instead of filtering on it, and this decides
 * which of the three it is. Kept pure and separate from the wizard so it can
 * be tested without rendering a five-step form.
 */

export type PoolOption = {
  poolId: string;
  poolName: string;
  /** One of agent_servers' six fixed words; only `ready` can take an agent. */
  status: string;
  freeSlots: number;
};

export type PoolAvailability =
  /** At least one ready pool has a free slot — the option is selectable. */
  | { state: "available"; pools: PoolOption[] }
  /** No shared pool exists at all. */
  | { state: "none"; message: string }
  /** A pool exists but is not ready — resizing would not help. */
  | { state: "not-ready"; message: string }
  /** Every ready pool is full. */
  | { state: "full"; message: string };

/** The pools a manager may actually place an agent on. Ready AND has room —
 * unchanged by us-57.10, which widened what is *reported*, not what is
 * allowed. */
export function selectablePools(pools: PoolOption[]): PoolOption[] {
  return pools.filter((p) => p.status === "ready" && p.freeSlots > 0);
}

function nameList(pools: PoolOption[]): string {
  const names = pools.map((p) => p.poolName).filter(Boolean);
  if (names.length === 0) return "The agent pool";
  if (names.length === 1) return names[0];
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

export function poolAvailability(pools: PoolOption[]): PoolAvailability {
  const selectable = selectablePools(pools);
  if (selectable.length > 0) return { state: "available", pools: selectable };

  if (pools.length === 0) {
    return {
      state: "none",
      message:
        "No agent pool exists yet — ask the superadmin to provision one.",
    };
  }

  const ready = pools.filter((p) => p.status === "ready");
  if (ready.length === 0) {
    // Naming it matters: "a pool" and "the pool you were told about last
    // week" are the same thing to a superadmin only if the wizard says which.
    const unready = pools.filter((p) => p.status !== "ready");
    return {
      state: "not-ready",
      message:
        `${nameList(unready)} is not ready right now — ask the superadmin ` +
        "to check it. Resizing will not help.",
    };
  }

  return {
    state: "full",
    message:
      `${nameList(ready)} is full — ask the superadmin to resize it or add ` +
      "another pool.",
  };
}
