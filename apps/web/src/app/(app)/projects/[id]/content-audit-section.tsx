"use client";

import { Fragment, useMemo, useState } from "react";
import { ChevronDown, ChevronRight, History } from "lucide-react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { EmptyState } from "@/components/empty-state";
import { formatWhen } from "@/lib/approvals";

export type ContentAuditRow = {
  id: string;
  surface: string;
  item_key: string;
  action: string;
  actor_type: string;
  actor_name: string;
  before_text: string | null;
  after_text: string | null;
  created_at: string;
};

const ANY = "__any__";
const MAX_SHOWN_CHARS = 2000;

const SURFACE_LABELS: Record<string, string> = {
  project: "Overview",
  // us-100.3: one vocabulary. `guidelines` is the storage key for the
  // Agent Instructions document; `worker_instructions` is the per-task set.
  guidelines: "Agent Instructions",
  learnings: "Learnings",
  worker_instructions: "Task Instructions",
};

function surfaceLabel(surface: string) {
  return SURFACE_LABELS[surface] ?? surface;
}

function clip(text: string) {
  if (text.length <= MAX_SHOWN_CHARS) return text;
  return `${text.slice(0, MAX_SHOWN_CHARS)}\n… (${
    text.length - MAX_SHOWN_CHARS
  } more characters)`;
}

/** Chronological change log for the four steering surfaces (us-5.33):
 * who changed the Overview, Guidelines, Learnings, or Worker
 * instructions, expandable to a before → after view. */
export function ContentAuditSection({
  rows,
  initialSurface,
}: {
  rows: ContentAuditRow[];
  initialSurface?: string;
}) {
  const [surface, setSurface] = useState(
    initialSurface && SURFACE_LABELS[initialSurface] ? initialSurface : ANY
  );
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  const surfaceItems = useMemo(
    () => [
      { label: "All surfaces", value: ANY },
      ...Object.entries(SURFACE_LABELS).map(([value, label]) => ({
        label,
        value,
      })),
    ],
    []
  );

  const filtered = useMemo(
    () => rows.filter((r) => surface === ANY || r.surface === surface),
    [rows, surface]
  );

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Content changes</CardTitle>
        <CardDescription>
          Every change to the Overview, Guidelines, Learnings, and Worker
          instructions — who, when, and what it said before and after.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <div className="flex flex-wrap items-end gap-3">
          <div className="grid gap-1.5">
            <Label className="text-xs text-muted-foreground">Surface</Label>
            <Select
              items={surfaceItems}
              value={surface}
              onValueChange={(v) => {
                if (typeof v === "string") setSurface(v);
              }}
            >
              <SelectTrigger size="sm" className="w-48">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {surfaceItems.map((i) => (
                  <SelectItem key={i.value} value={i.value}>
                    {i.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          {surface !== ANY && (
            <Button variant="ghost" size="sm" onClick={() => setSurface(ANY)}>
              Clear
            </Button>
          )}
        </div>

        {rows.length === 0 ? (
          <EmptyState
            icon={History}
            title="No content changes recorded yet"
            description="Edits to the project overview, guidelines, learnings, and agent instructions will show up here."
          />
        ) : filtered.length === 0 ? (
          <EmptyState
            icon={History}
            title="No changes on this surface yet"
            description="Try another surface, or clear the filter."
          />
        ) : (
          <>
            {/* US-35.7: same bare-fragment table, same tablet overflow. */}
            <div className="min-w-0 rounded-lg border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-8" />
                  <TableHead className="hidden lg:table-cell">When</TableHead>
                  <TableHead>Surface</TableHead>
                  <TableHead className="w-full max-w-0">Item</TableHead>
                  <TableHead>Change</TableHead>
                  <TableHead className="hidden lg:table-cell">By</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filtered.map((r) => {
                  const open = Boolean(expanded[r.id]);
                  return (
                    <Fragment key={r.id}>
                      <TableRow
                        className="cursor-pointer"
                        onClick={() =>
                          setExpanded((e) => ({ ...e, [r.id]: !open }))
                        }
                      >
                        <TableCell className="text-muted-foreground">
                          {open ? (
                            <ChevronDown className="size-3.5" />
                          ) : (
                            <ChevronRight className="size-3.5" />
                          )}
                        </TableCell>
                        <TableCell className="hidden text-muted-foreground lg:table-cell">
                          {formatWhen(r.created_at)}
                        </TableCell>
                        <TableCell>{surfaceLabel(r.surface)}</TableCell>
                        <TableCell className="w-full max-w-0">
                          <span className="block truncate">
                            {r.item_key || "—"}
                          </span>
                          {/* US-35.7: When and By are columns only at `lg`. */}
                          <span className="block truncate text-xs text-muted-foreground lg:hidden">
                            {formatWhen(r.created_at)}
                            {r.actor_name ? ` · ${r.actor_name}` : ""}
                          </span>
                        </TableCell>
                        <TableCell>{r.action}</TableCell>
                        <TableCell className="hidden text-muted-foreground lg:table-cell">
                          {r.actor_name || "—"}
                          {r.actor_type !== "user" && (
                            <span className="ml-1 text-xs">
                              ({r.actor_type})
                            </span>
                          )}
                        </TableCell>
                      </TableRow>
                      {open && (
                        <TableRow className="hover:bg-transparent">
                          <TableCell />
                          <TableCell colSpan={5}>
                            <div className="grid gap-3 py-1 md:grid-cols-2">
                              <div>
                                <p className="mb-1 text-xs font-medium text-muted-foreground">
                                  Before
                                </p>
                                <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded-md bg-muted/50 p-2 text-xs">
                                  {r.before_text ? clip(r.before_text) : "—"}
                                </pre>
                              </div>
                              <div>
                                <p className="mb-1 text-xs font-medium text-muted-foreground">
                                  After
                                </p>
                                <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded-md bg-muted/50 p-2 text-xs">
                                  {r.after_text ? clip(r.after_text) : "—"}
                                </pre>
                              </div>
                            </div>
                          </TableCell>
                        </TableRow>
                      )}
                    </Fragment>
                  );
                })}
              </TableBody>
            </Table>
            </div>
            <p className="text-xs text-muted-foreground">
              {filtered.length} of {rows.length} change
              {rows.length === 1 ? "" : "s"}
            </p>
          </>
        )}
      </CardContent>
    </Card>
  );
}
