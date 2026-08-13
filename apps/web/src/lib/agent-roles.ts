/** US-77.1: what an agent does is FOUR roles, not ten run kinds.
 *
 * `runner_config.enabled_kinds` still stores run kinds — the claim predicate
 * (`db.py`'s `rc.enabled_kinds ? r.kind`) and the runner's own gate read that
 * array and nothing else. What changed is the vocabulary the manager works in:
 * "guidelines refresh" and "story elaboration" are steps of planning, not
 * decisions, and the only thing the extra granularity ever produced was a
 * benched agent — an unchecked kind means work sits in the pool unclaimed and
 * nothing errors.
 *
 * us-61.2 already grouped the ten checkboxes under these four headings. This
 * makes the heading the control and the kinds an implementation detail.
 *
 * This file is the source of truth for both: `agent-runner-data.ts` derives
 * its `ROUTE_KINDS`/`DISPATCH_KINDS` from it, so a kind cannot be routable and
 * role-less at the same time.
 */

export type AgentRoleKey = "planning" | "programming" | "testing" | "deployment";

export type AgentRole = {
  key: AgentRoleKey;
  label: string;
  /** What taking this role actually lets the agent claim, in the manager's
   *  terms — a role is a promise about work, not a word. */
  help: string;
  /** The dispatchable run kinds it covers, in pipeline order. Labels stay
   *  here because a single run is still shown by its kind (a `code` run reads
   *  "Code" in the queue); only the grant is by role. */
  kinds: { key: string; label: string }[];
};

export const AGENT_ROLES: AgentRole[] = [
  {
    key: "planning",
    label: "Planning",
    help: "PRDs, story breakdown, implementation and test plans, wireframes, guidelines and story elaboration.",
    kinds: [
      { key: "prd", label: "PRD" },
      { key: "breakdown", label: "Breakdown" },
      { key: "plan", label: "Plan" },
      { key: "guidelines", label: "Guidelines refresh" },
      { key: "elaborate", label: "Story elaboration" },
      // Moved out of us-61.2's "Programming" group: a wireframe draws what a
      // story should look like before it is built, and hands back over MCP
      // with no repository at all.
      { key: "wireframe", label: "Wireframe" },
    ],
  },
  {
    key: "programming",
    label: "Programming",
    help: "Writes the code for an approved plan and hands back a changeset.",
    kinds: [{ key: "code", label: "Code" }],
  },
  {
    key: "testing",
    label: "Testing",
    help: "Claims verification runs and reports per-case results.",
    kinds: [{ key: "test", label: "Test" }],
  },
  {
    key: "deployment",
    label: "Deployment",
    help: "Prepares release cuts and promotion PRs, and executes deployments under the deployment's own rails.",
    kinds: [
      { key: "release", label: "Release" },
      { key: "deploy", label: "Deploy" },
    ],
  },
];

/** Every dispatchable kind, in role order. Must stay equal to the API's
 *  `ROUTE_KINDS` in `runner_socket.py` (minus `brain`, which is the runner's
 *  own reasoning rather than a run) — `agent-roles.test.ts` pins that. */
export const ROLE_KINDS: { key: string; label: string }[] = AGENT_ROLES.flatMap(
  (r) => r.kinds
);

export const ALL_ROLE_KEYS: AgentRoleKey[] = AGENT_ROLES.map((r) => r.key);

/** The role a run kind belongs to — for labelling a stored kind the UI no
 *  longer offers individually. */
export function roleOfKind(kind: string): AgentRole | undefined {
  return AGENT_ROLES.find((r) => r.kinds.some((k) => k.key === kind));
}

/** What to store in `enabled_kinds` for a set of checked roles. Order follows
 *  `AGENT_ROLES` so two agents with the same roles store the same array. */
export function kindsForRoles(roles: readonly string[]): string[] {
  return AGENT_ROLES.filter((r) => roles.includes(r.key)).flatMap((r) =>
    r.kinds.map((k) => k.key)
  );
}

/**
 * Which boxes to tick for a stored `enabled_kinds`.
 *
 * `null`/`undefined` means every kind — the us-53.4 rule that a never-saved
 * agent is unrestricted rather than benched, kept so no backfill was needed.
 *
 * A role is checked when **any** of its kinds is stored: a legacy config with
 * `plan` but not `elaborate` was an agent that plans, and reading it as "not
 * Planning" would silently bench work it has been claiming. The first save
 * through this UI writes the role whole.
 */
export function rolesForKinds(
  kinds: readonly string[] | null | undefined
): AgentRoleKey[] {
  if (kinds == null) return [...ALL_ROLE_KEYS];
  return AGENT_ROLES.filter((r) => r.kinds.some((k) => kinds.includes(k.key))).map(
    (r) => r.key
  );
}

/** True when a stored array only covers part of a checked role — the case
 *  where saving will widen what the agent claims, so the UI can say so. */
export function rolesArePartial(
  kinds: readonly string[] | null | undefined
): boolean {
  if (kinds == null) return false;
  return AGENT_ROLES.some(
    (r) =>
      r.kinds.some((k) => kinds.includes(k.key)) &&
      !r.kinds.every((k) => kinds.includes(k.key))
  );
}

/** How to read a stored `enabled_kinds` out loud: "Planning, Testing". */
export function roleLabelsForKinds(
  kinds: readonly string[] | null | undefined
): string[] {
  const keys = rolesForKinds(kinds);
  return AGENT_ROLES.filter((r) => keys.includes(r.key)).map((r) => r.label);
}
