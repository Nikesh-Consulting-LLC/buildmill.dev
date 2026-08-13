// US-35.2: the agent-hosting facts about a machine.
//
// This was `agent-servers/host-card.tsx`, whose `HostCard` rendered a second
// machine card beside the one on /servers — the same box, two cards, two pages.
// The card is gone: the machine list now carries a compact agent strip, and the
// hardware readout it duplicated lives on the machine's Overview tab, which
// `host-detail.tsx` already had. What remains is what more than one surface
// genuinely shares.

export type AgentServerRow = {
  id: string;
  org_id: string;
  server_id: string;
  status: "new" | "provisioning" | "ready" | "degraded" | "error" | "removed";
  workdir: string;
  modules: string[];
  extra_packages: string[];
  setup_commands: string;
  cli_versions: Record<string, string> | null;
  allow_agent_sudo: boolean;
  // US-68.3: on by default; only a platform admin may change it.
  auto_repair_enabled: boolean;
  slot_template: Record<string, unknown> | null;
  bundle_hash: string | null;
  agent_version: string | null;
  provisioned_at: string | null;
  os_release: string | null;
  cpu_count: number | null;
  mem_total_mb: number | null;
  mem_free_mb: number | null;
  disk_total_gb: number | null;
  disk_free_gb: number | null;
  load_avg: number | null;
  last_probe_at: string | null;
  probe_error: string | null;
  // US-57.1: a shared machine IS a pool — set only from /admin/machines.
  shared: boolean;
  pool_name: string | null;
  capacity: number | null;
  servers: { id: string; name: string; host: string; port: number; username: string } | null;
};

export type SlotRow = {
  id: string;
  org_id: string;
  agent_server_id: string;
  slot_index: number;
  name: string;
  worker_id: string | null;
  principal_id: string | null;
  service_name: string;
  workspace_path: string;
  desired_state: "paused" | "enabled" | "stopped";
  service_state: "active" | "failed" | "inactive" | "unknown" | null;
  last_service_check: string | null;
  agent_version: string | null;
  status: "active" | "removed";
  // US-68.3: the auto-repair ladder gave up on this slot without it
  // recovering — a human needs to look, rather than the sweep retrying
  // forever.
  auto_repair_needs_attention: boolean;
};

export const STATUS_STYLES: Record<string, string> = {
  new: "bg-muted text-muted-foreground",
  provisioning: "bg-blue-100 text-blue-700 dark:bg-blue-950 dark:text-blue-300",
  ready: "bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300",
  degraded: "bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-300",
  error: "bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-300",
};

export const STATUS_LABELS: Record<string, string> = {
  new: "Registered — not installed",
  provisioning: "Provisioning",
  ready: "Ready",
  degraded: "Degraded",
  error: "Error",
};

/** A probe older than this is stale — shown as such, never as current data. */
const PROBE_STALE_MS = 15 * 60 * 1000;

export function probeAge(
  lastProbeAt: string | null
): { label: string; stale: boolean } | null {
  if (!lastProbeAt) return null;
  const ms = Date.now() - new Date(lastProbeAt).getTime();
  const stale = ms > PROBE_STALE_MS;
  const mins = Math.round(ms / 60000);
  const label =
    mins < 1 ? "just now" : mins < 60 ? `${mins}m ago` : `${Math.round(mins / 60)}h ago`;
  return { label, stale };
}
