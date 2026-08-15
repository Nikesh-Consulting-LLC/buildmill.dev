"use client";

import { cn } from "@/lib/utils";

/** US-2.32: the full lifecycle — every path a work item can take, who does
 * what at each block. Static SVG, hand-laid: the five stage bands frame it,
 * the happy path runs down the spine, rework loops hang off the review
 * diamonds on the right, failed/cancelled exits sit on the left as dashed
 * exception paths. All labels are structural (code); only the section's
 * intro line is superadmin-editable, per the us-2.30 split. */

type Tone = "person" | "agent" | "trigger" | "done" | "failure" | "muted";

const BOX_STYLES: Record<Tone, string> = {
  person: "fill-amber-50 stroke-amber-300 dark:fill-amber-950 dark:stroke-amber-700",
  agent: "fill-blue-50 stroke-blue-300 dark:fill-blue-950 dark:stroke-blue-700",
  trigger:
    "fill-emerald-50 stroke-emerald-300 dark:fill-emerald-950 dark:stroke-emerald-700",
  done: "fill-emerald-600 dark:fill-emerald-700",
  failure: "fill-red-50 stroke-red-300 dark:fill-red-950 dark:stroke-red-800",
  muted: "fill-muted stroke-border",
};

const LABEL_STYLES: Record<Tone, string> = {
  person: "fill-amber-900 dark:fill-amber-200",
  agent: "fill-blue-900 dark:fill-blue-200",
  trigger: "fill-emerald-900 dark:fill-emerald-200",
  done: "fill-white",
  failure: "fill-red-900 dark:fill-red-200",
  muted: "fill-muted-foreground",
};

type Rect = {
  x: number;
  y: number;
  w?: number;
  tone: Tone;
  label: string;
  sub?: string;
};

const H = 44;

const RECTS: Rect[] = [
  { x: 165, y: 20, tone: "person", label: "Define", sub: "you write the work item" },
  { x: 480, y: 98, tone: "agent", label: "PRD draft", sub: "the factory writes it" },
  { x: 480, y: 278, tone: "agent", label: "Story breakdown", sub: "the factory splits it" },
  { x: 165, y: 278, tone: "person", label: "Dispatch planning", sub: "you" },
  // us-96.5: the think-first stage speaks per type — a bug's artifact is a
  // root cause analysis (us-96.2), and a chore has no such stage at all
  // (us-96.1; see the edge label below).
  { x: 165, y: 370, tone: "agent", label: "Plan", sub: "plans · a bug's RCA" },
  { x: 165, y: 550, tone: "person", label: "Dispatch code", sub: "you" },
  { x: 165, y: 630, tone: "agent", label: "Build", sub: "code on a branch" },
  { x: 165, y: 713, tone: "agent", label: "Test", sub: "runs the test plan" },
  { x: 165, y: 887, tone: "trigger", label: "Merged" },
  { x: 165, y: 998, tone: "trigger", label: "Deploy to UAT", sub: "you trigger · factory runs" },
  { x: 165, y: 1170, tone: "person", label: "Approve promotion", sub: "you" },
  { x: 165, y: 1248, tone: "trigger", label: "Production deploy", sub: "you trigger · factory runs" },
  { x: 165, y: 1326, tone: "done", label: "Done" },
  { x: 20, y: 370, w: 110, tone: "failure", label: "Run failed", sub: "re-dispatch" },
  { x: 20, y: 803, w: 110, tone: "muted", label: "Cancelled", sub: "before merge" },
];

type Diamond = {
  cx: number;
  cy: number;
  neutral?: boolean;
  label: string;
  sub?: string;
};

const DIAMONDS: Diamond[] = [
  { cx: 250, cy: 120, neutral: true, label: "Feature?", sub: "with a PRD" },
  { cx: 565, cy: 208, label: "PRD review", sub: "your call" },
  { cx: 250, cy: 487, label: "Plan review", sub: "your call" },
  { cx: 250, cy: 825, label: "Code review", sub: "your call" },
  { cx: 250, cy: 1108, label: "QA sign-off", sub: "your call" },
];

type Edge = {
  d: string;
  dashed?: boolean;
  label?: string;
  at?: [number, number];
  anchor?: "start" | "middle";
  rotate?: boolean;
};

const EDGES: Edge[] = [
  { d: "M250 64 V92" },
  { d: "M250 148 V274", label: "story · bug (a chore skips to Build)", at: [258, 205], anchor: "start" },
  { d: "M325 120 H476", label: "feature", at: [400, 112], anchor: "middle" },
  { d: "M565 142 V176" },
  { d: "M640 208 H684 V120 H654", label: "send back", at: [694, 164], rotate: true },
  { d: "M565 236 V274", label: "approve", at: [573, 258], anchor: "start" },
  { d: "M480 300 H339", label: "each story joins the rail", at: [410, 292], anchor: "middle" },
  { d: "M250 322 V366" },
  { d: "M250 414 V455" },
  { d: "M325 487 H460 V392 H343", label: "reject · re-plan", at: [466, 444], anchor: "start" },
  { d: "M250 515 V546", label: "approve", at: [258, 536], anchor: "start" },
  { d: "M250 594 V626" },
  { d: "M250 674 V709" },
  { d: "M250 757 V793" },
  { d: "M325 825 H460 V652 H343", label: "reject · fix run", at: [466, 742], anchor: "start" },
  { d: "M250 853 V883", label: "approve & merge", at: [258, 872], anchor: "start" },
  { d: "M250 931 V994" },
  { d: "M250 1042 V1076" },
  { d: "M325 1108 H440 V668 H343", label: "fail · send back", at: [446, 895], anchor: "start" },
  { d: "M250 1136 V1166", label: "sign off", at: [258, 1156], anchor: "start" },
  { d: "M250 1214 V1244" },
  { d: "M250 1292 V1322" },
  { d: "M165 392 H134", dashed: true },
  { d: "M165 735 H75 V418", dashed: true },
  { d: "M175 825 H134", dashed: true },
];

const ZONES: { label: string; y: number; h: number; shaded?: boolean }[] = [
  { label: "Draft", y: 8, h: 342, shaded: true },
  { label: "Plan", y: 350, h: 260 },
  { label: "Build", y: 610, h: 370, shaded: true },
  { label: "UAT", y: 980, h: 170 },
  { label: "Release", y: 1150, h: 242, shaded: true },
];

const LEGEND: { tone: Tone; label: string }[] = [
  { tone: "person", label: "You" },
  { tone: "agent", label: "The factory" },
  { tone: "trigger", label: "You trigger · the factory runs" },
];

function RectNode({ node }: { node: Rect }) {
  const w = node.w ?? 170;
  const cx = node.x + w / 2;
  return (
    <g>
      <rect
        x={node.x}
        y={node.y}
        width={w}
        height={H}
        rx={10}
        className={BOX_STYLES[node.tone]}
      />
      <text
        x={cx}
        y={node.y + (node.sub ? 19 : 27)}
        textAnchor="middle"
        className={cn("text-[12px] font-semibold", LABEL_STYLES[node.tone])}
      >
        {node.label}
      </text>
      {node.sub && (
        <text
          x={cx}
          y={node.y + 33}
          textAnchor="middle"
          className="fill-muted-foreground text-[9px]"
        >
          {node.sub}
        </text>
      )}
    </g>
  );
}

function DiamondNode({ node }: { node: Diamond }) {
  const { cx, cy } = node;
  const points = `${cx},${cy - 28} ${cx + 75},${cy} ${cx},${cy + 28} ${cx - 75},${cy}`;
  return (
    <g>
      <polygon
        points={points}
        className={cn(
          "fill-card",
          node.neutral
            ? "stroke-border"
            : "stroke-amber-400/80 dark:stroke-amber-600"
        )}
      />
      <text
        x={cx}
        y={cy - 1}
        textAnchor="middle"
        className="fill-foreground text-[11px] font-semibold"
      >
        {node.label}
      </text>
      {node.sub && (
        <text
          x={cx}
          y={cy + 13}
          textAnchor="middle"
          className="fill-muted-foreground text-[9px]"
        >
          {node.sub}
        </text>
      )}
    </g>
  );
}

export function LifecycleFlowchart() {
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs text-muted-foreground">
        {LEGEND.map((item) => (
          <span key={item.label} className="inline-flex items-center gap-1.5">
            <svg width="14" height="14" aria-hidden>
              <rect
                x="1"
                y="1"
                width="12"
                height="12"
                rx="3"
                className={BOX_STYLES[item.tone]}
              />
            </svg>
            {item.label}
          </span>
        ))}
        <span className="inline-flex items-center gap-1.5">
          <svg width="20" height="6" aria-hidden>
            <line
              x1="0"
              y1="3"
              x2="20"
              y2="3"
              strokeDasharray="4 3"
              className="stroke-muted-foreground/70"
              strokeWidth="1.5"
            />
          </svg>
          exception path
        </span>
      </div>

      <div className="overflow-x-auto rounded-md border">
        <svg
          viewBox="0 0 720 1400"
          className="h-auto w-full min-w-[600px]"
          role="img"
          aria-label="Work item lifecycle: define, plan, build, test, review, merge, UAT, and release — with rework loops, failed-run re-dispatch, and cancel exits"
        >
          <defs>
            <marker
              id="lf-arrow"
              viewBox="0 0 10 10"
              refX="8"
              refY="5"
              markerWidth="7"
              markerHeight="7"
              orient="auto-start-reverse"
            >
              <path d="M0,0 L10,5 L0,10 z" className="fill-muted-foreground/70" />
            </marker>
          </defs>

          {ZONES.map((zone) => (
            <g key={zone.label}>
              <rect
                x={8}
                y={zone.y}
                width={704}
                height={zone.h}
                rx={10}
                className={zone.shaded ? "fill-muted/25" : "fill-transparent"}
              />
              <text
                x={24}
                y={zone.y + 22}
                className="fill-muted-foreground/80 text-[10px] font-medium uppercase tracking-wider"
              >
                {zone.label}
              </text>
            </g>
          ))}

          {EDGES.map((edge) => (
            <g key={edge.d}>
              <path
                d={edge.d}
                fill="none"
                strokeWidth={1.5}
                strokeDasharray={edge.dashed ? "5 4" : undefined}
                markerEnd="url(#lf-arrow)"
                className="stroke-muted-foreground/60"
              />
              {edge.label && edge.at && (
                <text
                  x={edge.at[0]}
                  y={edge.at[1]}
                  textAnchor={edge.rotate ? "middle" : (edge.anchor ?? "start")}
                  transform={
                    edge.rotate
                      ? `rotate(-90 ${edge.at[0]} ${edge.at[1]})`
                      : undefined
                  }
                  className="fill-muted-foreground text-[9px]"
                >
                  {edge.label}
                </text>
              )}
            </g>
          ))}

          {RECTS.map((node) => (
            <RectNode key={`${node.label}-${node.x}`} node={node} />
          ))}
          {DIAMONDS.map((node) => (
            <DiamondNode key={node.label} node={node} />
          ))}
        </svg>
      </div>
    </div>
  );
}
