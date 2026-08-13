"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { ScrollText } from "lucide-react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
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
import { StatusBadge, type IssueStatus } from "@/components/status-badge";
import { formatWhen, humanizeToken, type ApprovalRow } from "@/lib/approvals";

export type AuditRow = ApprovalRow & {
  issue_id: string;
  issue_title: string;
};

const ANY = "__any__";

type Option = { label: string; value: string };

/** Filterable approvals table across a project's issues (us-2.7). */
export function AuditTab({
  rows,
  actorNames,
  backToProject,
}: {
  rows: AuditRow[];
  actorNames: Record<string, string>;
  /** Query string (no leading `?`) so a work item's breadcrumb returns here. */
  backToProject: string;
}) {
  const [gate, setGate] = useState(ANY);
  const [decision, setDecision] = useState(ANY);
  const [actor, setActor] = useState(ANY);
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");

  // Options are derived from the rows themselves, never from a hardcoded
  // gate list — a gate that starts emitting rows becomes filterable on its own.
  const gateItems = useMemo<Option[]>(
    () => [
      { label: "All gates", value: ANY },
      ...Array.from(new Set(rows.map((r) => r.gate)))
        .sort()
        .map((g) => ({ label: humanizeToken(g), value: g })),
    ],
    [rows]
  );

  const decisionItems = useMemo<Option[]>(
    () => [
      { label: "All decisions", value: ANY },
      ...Array.from(new Set(rows.map((r) => r.decision)))
        .sort()
        .map((d) => ({ label: humanizeToken(d), value: d })),
    ],
    [rows]
  );

  const actorItems = useMemo<Option[]>(
    () => [
      { label: "All actors", value: ANY },
      ...Array.from(
        new Set(rows.map((r) => r.actor).filter((a): a is string => Boolean(a)))
      )
        .map((a) => ({ label: actorNames[a] ?? a, value: a }))
        .sort((x, y) => x.label.localeCompare(y.label)),
    ],
    [rows, actorNames]
  );

  const filtered = useMemo(() => {
    const fromMs = from ? new Date(`${from}T00:00:00`).getTime() : null;
    const toMs = to ? new Date(`${to}T23:59:59.999`).getTime() : null;
    return rows.filter((r) => {
      if (gate !== ANY && r.gate !== gate) return false;
      if (decision !== ANY && r.decision !== decision) return false;
      if (actor !== ANY && r.actor !== actor) return false;
      const ts = new Date(r.created_at).getTime();
      if (fromMs !== null && ts < fromMs) return false;
      if (toMs !== null && ts > toMs) return false;
      return true;
    });
  }, [rows, gate, decision, actor, from, to]);

  const isFiltered =
    gate !== ANY || decision !== ANY || actor !== ANY || from !== "" || to !== "";

  function clearFilters() {
    setGate(ANY);
    setDecision(ANY);
    setActor(ANY);
    setFrom("");
    setTo("");
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Audit</CardTitle>
        <CardDescription>
          Every gate decision across this project&apos;s issues — append-only,
          so &quot;why did we ship this&quot; stays answerable.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <div className="flex flex-wrap items-end gap-3">
          <div className="grid gap-1.5">
            <Label className="text-xs text-muted-foreground">Gate</Label>
            <Select
              items={gateItems}
              value={gate}
              onValueChange={(v) => {
                if (typeof v === "string") setGate(v);
              }}
            >
              <SelectTrigger size="sm" className="w-40">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {gateItems.map((i) => (
                  <SelectItem key={i.value} value={i.value}>
                    {i.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="grid gap-1.5">
            <Label className="text-xs text-muted-foreground">Decision</Label>
            <Select
              items={decisionItems}
              value={decision}
              onValueChange={(v) => {
                if (typeof v === "string") setDecision(v);
              }}
            >
              <SelectTrigger size="sm" className="w-40">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {decisionItems.map((i) => (
                  <SelectItem key={i.value} value={i.value}>
                    {i.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="grid gap-1.5">
            <Label className="text-xs text-muted-foreground">Actor</Label>
            <Select
              items={actorItems}
              value={actor}
              onValueChange={(v) => {
                if (typeof v === "string") setActor(v);
              }}
            >
              <SelectTrigger size="sm" className="w-48">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {actorItems.map((i) => (
                  <SelectItem key={i.value} value={i.value}>
                    {i.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="grid gap-1.5">
            <Label className="text-xs text-muted-foreground" htmlFor="audit-from">
              From
            </Label>
            <Input
              id="audit-from"
              type="date"
              value={from}
              onChange={(e) => setFrom(e.target.value)}
              className="w-36"
            />
          </div>

          <div className="grid gap-1.5">
            <Label className="text-xs text-muted-foreground" htmlFor="audit-to">
              To
            </Label>
            <Input
              id="audit-to"
              type="date"
              value={to}
              onChange={(e) => setTo(e.target.value)}
              className="w-36"
            />
          </div>

          {isFiltered && (
            <Button variant="ghost" size="sm" onClick={clearFilters}>
              Clear
            </Button>
          )}
        </div>

        {rows.length === 0 ? (
          <EmptyState
            icon={ScrollText}
            title="No decisions logged yet"
            description="Approvals, rejections, and overrides across this project's work items will show up here."
          />
        ) : filtered.length === 0 ? (
          <EmptyState
            icon={ScrollText}
            title="No matching decisions"
            description="No decisions match these filters. Try widening the gate, actor, or date range."
          />
        ) : (
          <>
            {/* US-35.7: the table rendered straight into a fragment, so nothing
                could constrain it and it pushed the page sideways on a tablet. */}
            <div className="min-w-0 rounded-lg border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="hidden lg:table-cell">When</TableHead>
                  <TableHead className="w-full max-w-0">Work item</TableHead>
                  <TableHead>Gate</TableHead>
                  <TableHead>Decision</TableHead>
                  <TableHead className="hidden lg:table-cell">Actor</TableHead>
                  <TableHead className="hidden whitespace-normal lg:table-cell">
                    Comment
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filtered.map((r) => (
                  <TableRow key={r.id}>
                    <TableCell className="hidden text-muted-foreground lg:table-cell">
                      {formatWhen(r.created_at)}
                    </TableCell>
                    <TableCell className="w-full max-w-0">
                      <Link
                        href={
                          // US-2.16: artifact decisions deep-link to the
                          // artifact's panel anchor on the issue page.
                          r.subject_type === "artifact" && r.subject_id
                            ? `/issues/${r.issue_id}?${backToProject}#artifact-${r.subject_id}`
                            : `/issues/${r.issue_id}?${backToProject}`
                        }
                        className="underline-offset-4 hover:underline"
                      >
                        {r.issue_title}
                      </Link>
                      {/* US-35.7: When, Actor and Comment are columns only at
                          `lg`; below it they ride here rather than vanish. */}
                      <span className="block truncate text-xs text-muted-foreground lg:hidden">
                        {formatWhen(r.created_at)}
                        {r.actor ? ` · ${actorNames[r.actor] ?? r.actor}` : ""}
                        {r.comment ? ` · ${r.comment}` : ""}
                      </span>
                    </TableCell>
                    <TableCell>{humanizeToken(r.gate)}</TableCell>
                    <TableCell>
                      <StatusBadge status={r.decision as IssueStatus} />
                    </TableCell>
                    <TableCell className="hidden lg:table-cell">
                      {r.actor ? (actorNames[r.actor] ?? r.actor) : "—"}
                    </TableCell>
                    <TableCell className="hidden max-w-sm truncate whitespace-normal text-muted-foreground lg:table-cell">
                      {r.comment || "—"}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            </div>
            <p className="text-xs text-muted-foreground">
              {filtered.length} of {rows.length} decision
              {rows.length === 1 ? "" : "s"}
            </p>
          </>
        )}
      </CardContent>
    </Card>
  );
}
