"use client";

import { useState } from "react";
import { cn } from "@/lib/utils";
import { Card, CardContent } from "@/components/ui/card";
import { MarkdownView } from "@/components/markdown-view";
import { ARCHITECTURE_NODES, type HelpNode } from "./help-content";
import type { HelpText } from "./use-help-text";

/** US-2.30: what runs where. One SVG, two shaded trust zones — the cloud
 * coordinates, code and credentials stay operator-side — with dashed flow
 * lines drifting in the data's direction (CSS only, stilled under
 * `prefers-reduced-motion`). Clicking a node explains it below the map. */

const NODE_W = 150;
const NODE_H = 56;

const POSITIONS: Record<HelpNode["key"], { x: number; y: number }> = {
  web: { x: 40, y: 40 },
  supabase: { x: 460, y: 40 },
  api: { x: 250, y: 128 },
  github: { x: 460, y: 128 },
  workers: { x: 250, y: 244 },
};

type Edge = {
  from: [number, number];
  to: [number, number];
  label: string;
  labelAt: [number, number];
};

const EDGES: Edge[] = [
  {
    from: [40 + NODE_W, 68],
    to: [460, 68],
    label: "data · realtime",
    labelAt: [325, 60],
  },
  {
    from: [115, 40 + NODE_H],
    to: [305, 128],
    label: "orchestrate",
    labelAt: [175, 122],
  },
  {
    from: [250 + NODE_W, 156],
    to: [460, 156],
    label: "PRs · merges",
    labelAt: [430, 148],
  },
  {
    from: [325, 128 + NODE_H],
    to: [325, 244],
    label: "claim · context · git push",
    labelAt: [415, 218],
  },
];

function Node({
  node,
  selected,
  onSelect,
}: {
  node: HelpNode;
  selected: boolean;
  onSelect: () => void;
}) {
  const { x, y } = POSITIONS[node.key];
  return (
    <g
      role="button"
      tabIndex={0}
      aria-pressed={selected}
      aria-label={node.label}
      className="cursor-pointer outline-none"
      onClick={onSelect}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onSelect();
        }
      }}
    >
      <rect
        x={x}
        y={y}
        width={NODE_W}
        height={NODE_H}
        rx={10}
        strokeWidth={selected ? 2 : 1}
        className={cn(
          "fill-card transition-[stroke]",
          selected ? "stroke-primary" : "stroke-border"
        )}
      />
      <g className="text-muted-foreground">
        <node.icon x={x + 12} y={y + 20} className="size-4" />
      </g>
      <text
        x={x + 34}
        y={y + 26}
        className="pointer-events-none fill-foreground text-[13px] font-semibold"
      >
        {node.label}
      </text>
      <text
        x={x + 34}
        y={y + 42}
        className="pointer-events-none fill-muted-foreground text-[10px]"
      >
        {node.sublabel}
      </text>
    </g>
  );
}

export function ArchitectureMap({ text }: { text: HelpText }) {
  const [selected, setSelected] = useState<HelpNode["key"]>("web");
  const node = ARCHITECTURE_NODES.find((n) => n.key === selected)!;

  return (
    <div className="flex flex-col gap-4">
      <div className="overflow-x-auto rounded-md border">
        <svg
          viewBox="0 0 660 324"
          className="h-auto w-full min-w-[560px]"
          aria-label="Build Mill architecture: web app, Supabase, API, and GitHub in the cloud; agents on the operator's machine"
        >
          {/* trust zones */}
          <rect
            x={8}
            y={8}
            width={644}
            height={196}
            rx={12}
            className="fill-muted/40 stroke-border"
            strokeDasharray="3 3"
          />
          <text
            x={24}
            y={28}
            className="fill-muted-foreground text-[10px] font-medium uppercase tracking-wider"
          >
            Cloud
          </text>
          <rect
            x={8}
            y={224}
            width={644}
            height={92}
            rx={12}
            className="fill-amber-500/10 stroke-amber-500/40"
            strokeDasharray="3 3"
          />
          <text
            x={24}
            y={244}
            className="fill-muted-foreground text-[10px] font-medium uppercase tracking-wider"
          >
            Operator&apos;s machine — repos · provider CLIs
          </text>

          {/* flow lines */}
          {EDGES.map((e) => (
            <g key={e.label}>
              <line
                x1={e.from[0]}
                y1={e.from[1]}
                x2={e.to[0]}
                y2={e.to[1]}
                strokeWidth={1.5}
                strokeDasharray="5 5"
                className="help-dash stroke-muted-foreground/50"
              />
              <text
                x={e.labelAt[0]}
                y={e.labelAt[1]}
                textAnchor="middle"
                className="fill-muted-foreground text-[10px]"
              >
                {e.label}
              </text>
            </g>
          ))}

          {ARCHITECTURE_NODES.map((n) => (
            <Node
              key={n.key}
              node={n}
              selected={n.key === selected}
              onSelect={() => setSelected(n.key)}
            />
          ))}
        </svg>
      </div>

      <Card>
        <CardContent className="flex flex-col gap-2 pt-4">
          <span className="flex items-center gap-2 text-base font-semibold">
            <node.icon className="size-4 text-muted-foreground" />
            {node.label}
          </span>
          <MarkdownView>{text(`help/architecture/${node.key}`)}</MarkdownView>
        </CardContent>
      </Card>
    </div>
  );
}
