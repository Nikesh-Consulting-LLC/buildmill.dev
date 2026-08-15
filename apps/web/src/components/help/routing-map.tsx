import { Bot, User } from "lucide-react";
import { cn } from "@/lib/utils";

/** us-96.5 rework (manager's UAT feedback, 2026-08-15): the Help section
 * SHOWS how items get routed — one lane per work-item type, because since
 * Phase 96 the type decides the path. Structural, like the lifecycle
 * flowchart: the lanes are code, not overridable prose, so they can only
 * change when the routing itself does.
 *
 * The visual language matches the stage tracker and the queue: blue = the
 * factory is working, amber = a gate waiting on you, green = landed. */

type Step = {
  label: string;
  /** Who acts here — mirrors the stage tracker's actor. */
  actor: "you" | "agent" | "done";
  /** A word under the chip when the label alone is ambiguous. */
  sub?: string;
};

type Lane = {
  type: string;
  title: string;
  /** The one sentence that explains this lane's shape. */
  note: string;
  steps: Step[];
};

const LANES: Lane[] = [
  {
    type: "feature",
    title: "Feature — and every story inside it",
    note:
      "The feature is the steering wheel: its stories are planned as a " +
      "batch and built as ONE feature-owned run and PR. A child never " +
      "routes individually — except a story in trouble (failed or " +
      "needs-fixes), or one being re-planned after it was planned once.",
    steps: [
      { label: "Draft", actor: "you", sub: "the requirement" },
      { label: "PRD", actor: "agent" },
      { label: "PRD review", actor: "you" },
      { label: "Stories", actor: "agent", sub: "the split" },
      { label: "Plan all", actor: "agent", sub: "one per story" },
      { label: "Approve plans", actor: "you", sub: "per story" },
      { label: "Build", actor: "agent", sub: "one run, one PR" },
      { label: "Code review", actor: "you" },
      { label: "Done", actor: "done" },
    ],
  },
  {
    type: "story",
    title: "Standalone story",
    note:
      "No PRD and no parent feature — the story and its acceptance " +
      "criteria are the whole contract, and it routes on its own.",
    steps: [
      { label: "Draft", actor: "you" },
      { label: "Plan", actor: "agent" },
      { label: "Plan review", actor: "you" },
      { label: "Build", actor: "agent" },
      { label: "Code review", actor: "you" },
      { label: "Merged", actor: "done" },
    ],
  },
  {
    type: "bug",
    title: "Bug",
    note:
      "The think-first run is a root cause analysis in plain language — " +
      "what broke, why, and the proposed fix, no code. Approving the RCA " +
      "unlocks the fix run, and the repro becomes the regression case.",
    steps: [
      { label: "Draft", actor: "you", sub: "the report" },
      { label: "RCA", actor: "agent", sub: "diagnosis" },
      { label: "RCA review", actor: "you" },
      { label: "Fix", actor: "agent" },
      { label: "Code review", actor: "you" },
      { label: "Merged", actor: "done" },
    ],
  },
  {
    type: "chore",
    title: "Chore",
    note:
      "Single-shot: Dispatch builds it. No plan phase exists — the one " +
      "gate with teeth, code review, is the whole ceremony.",
    steps: [
      { label: "Draft", actor: "you" },
      { label: "Build", actor: "agent" },
      { label: "Code review", actor: "you" },
      { label: "Merged", actor: "done" },
    ],
  },
];

/** The rules that decide WHEN a lane may move — the two switches and the
 * serial law (US-86.1), plus Phase 96's feature-ownership refusals. */
const RULES: { title: string; body: string }[] = [
  {
    title: "The feature owns its stories",
    body:
      "With “Route feature as one” on (the default), pressing Plan or " +
      "Code on a healthy feature child is refused — “FEAT-2.3 owns the " +
      "plan — dispatch the feature to plan all 5 stories.” The feature " +
      "page carries the batch buttons. Trouble (failed / needs-fixes) and " +
      "re-planning stay individual, so one stuck story never wedges the " +
      "batch.",
  },
  {
    title: "One item at a time",
    body:
      "A project works ONE routing unit from first claim to merged — a " +
      "story, or a whole feature when the switch groups them. Everything " +
      "queued behind it is held with an hourglass and a sentence naming " +
      "the blocker, never refused: queueing is always legal, starting is " +
      "what waits.",
  },
  {
    title: "Build order picks who is next",
    body:
      "With “Follow build order” on (the default), the queue drains in " +
      "Epic → Feature → Story order. Off, it drains in the order you " +
      "dispatched. Either way the hourglass names exactly which item is " +
      "ahead.",
  },
  {
    title: "Where to set it",
    body:
      "Project → Worker instructions → Task processing holds both " +
      "switches and the auto-approve gates. The instruction text each " +
      "lane's agent receives is on the same page — one entry per type " +
      "since Phase 96.",
  },
];

const ACTOR_STYLE: Record<Step["actor"], string> = {
  you: "border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-200",
  agent:
    "border-blue-200 bg-blue-50 text-blue-900 dark:border-blue-900 dark:bg-blue-950 dark:text-blue-200",
  done: "border-emerald-200 bg-emerald-50 text-emerald-900 dark:border-emerald-900 dark:bg-emerald-950 dark:text-emerald-200",
};

function Chip({ step }: { step: Step }) {
  return (
    <span className="flex shrink-0 flex-col items-center gap-0.5">
      <span
        className={cn(
          "flex items-center gap-1 whitespace-nowrap rounded-full border px-2.5 py-0.5 text-xs font-medium",
          ACTOR_STYLE[step.actor]
        )}
      >
        {step.actor === "you" && <User className="size-3" />}
        {step.actor === "agent" && <Bot className="size-3" />}
        {step.label}
      </span>
      {step.sub && (
        <span className="whitespace-nowrap text-[10px] text-muted-foreground">
          {step.sub}
        </span>
      )}
    </span>
  );
}

export function RoutingMap() {
  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-4 text-xs text-muted-foreground">
        <span className="flex items-center gap-1">
          <User className="size-3 text-amber-600 dark:text-amber-400" />
          a gate waiting on you
        </span>
        <span className="flex items-center gap-1">
          <Bot className="size-3 text-blue-600 dark:text-blue-400" />
          the factory works
        </span>
      </div>

      {LANES.map((lane) => (
        <div key={lane.type} className="rounded-lg border p-3">
          <p className="text-sm font-medium">{lane.title}</p>
          <p className="mt-0.5 text-xs text-muted-foreground">{lane.note}</p>
          <div className="mt-2.5 flex items-start gap-1.5 overflow-x-auto pb-1">
            {lane.steps.map((step, i) => (
              <span key={step.label} className="flex items-start gap-1.5">
                {i > 0 && (
                  <span className="mt-1 shrink-0 text-muted-foreground/50">
                    →
                  </span>
                )}
                <Chip step={step} />
              </span>
            ))}
          </div>
        </div>
      ))}

      <div className="grid gap-3 sm:grid-cols-2">
        {RULES.map((rule) => (
          <div key={rule.title} className="rounded-md border px-3 py-2">
            <p className="text-sm font-medium">{rule.title}</p>
            <p className="mt-0.5 text-sm text-muted-foreground">{rule.body}</p>
          </div>
        ))}
      </div>
    </div>
  );
}
