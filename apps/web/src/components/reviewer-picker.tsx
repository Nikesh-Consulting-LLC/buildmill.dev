"use client";

import { useEffect, useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import { Bot, User } from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

type Principal = { id: string; kind: "human" | "agent"; label: string };

const UNASSIGNED = "unassigned";

// US-9.10: route a run to a named reviewer — a principal with review_work
// (owner/admin/lead/reviewer), human or agent. Self-contained: loads the org's
// review-capable principals and whether the caller may route (manage_work or
// review_work). The guard_run_reviewer trigger is authoritative.
export function ReviewerPicker({
  runId,
  orgId,
  reviewerId,
}: {
  runId: string;
  orgId: string;
  reviewerId: string | null;
}) {
  const router = useRouter();
  const [principals, setPrincipals] = useState<Principal[]>([]);
  const [canRoute, setCanRoute] = useState(false);
  const [value, setValue] = useState(reviewerId ?? UNASSIGNED);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      const supabase = createClient();
      const {
        data: { user },
      } = await supabase.auth.getUser();

      // review-capable principals: role grants review_work.
      const { data: caps } = await supabase
        .from("role_capabilities")
        .select("role")
        .eq("capability", "review_work")
        .eq("allowed", true);
      const reviewRoles = new Set((caps ?? []).map((c) => c.role as string));

      const { data: members } = await supabase
        .from("organization_members")
        .select("role, principal_id, principals(kind, email, display_name)")
        .eq("org_id", orgId)
        .eq("status", "active");

      const rows = (members ?? []) as unknown as Array<{
        role: string;
        principal_id: string;
        principals: { kind: "human" | "agent"; email: string | null; display_name: string | null } | null;
      }>;
      setPrincipals(
        rows
          .filter((m) => reviewRoles.has(m.role))
          .map((m) => ({
            id: m.principal_id,
            kind: m.principals?.kind ?? "human",
            label: m.principals?.display_name || m.principals?.email || "Member",
          })),
      );

      if (user) {
        const { data: myRow } = await supabase
          .from("organization_members")
          .select("role")
          .eq("org_id", orgId)
          .eq("user_id", user.id)
          .maybeSingle();
        const myRole = (myRow as { role?: string } | null)?.role;
        if (myRole) {
          const { data: myCaps } = await supabase
            .from("role_capabilities")
            .select("capability, allowed")
            .eq("role", myRole)
            .in("capability", ["manage_work", "review_work"]);
          setCanRoute((myCaps ?? []).some((c) => c.allowed));
        }
      }
    })();
  }, [orgId]);

  async function handleChange(next: string) {
    setValue(next);
    setBusy(true);
    setError(null);
    const supabase = createClient();
    const { error: dbError } = await supabase
      .from("runs")
      .update({ reviewer_id: next === UNASSIGNED ? null : next })
      .eq("id", runId);
    if (dbError) {
      setError(dbError.message);
      setValue(reviewerId ?? UNASSIGNED);
    } else {
      router.refresh();
    }
    setBusy(false);
  }

  const current = principals.find((p) => p.id === value);

  if (!canRoute) {
    if (value === UNASSIGNED) return null;
    return (
      <span className="inline-flex items-center gap-1.5 text-sm text-muted-foreground">
        Waiting on
        <span className="inline-flex items-center gap-1 font-medium text-foreground">
          {current?.kind === "agent" ? <Bot className="size-3.5" /> : <User className="size-3.5" />}
          {current?.label ?? "reviewer"}
        </span>
      </span>
    );
  }

  return (
    <div className="flex items-center gap-2">
      <span className="text-sm text-muted-foreground">Reviewer</span>
      <Select
        items={[
          { value: UNASSIGNED, label: "Unassigned" },
          ...principals.map((p) => ({ value: p.id, label: p.label })),
        ]}
        value={value}
        onValueChange={(v) => typeof v === "string" && v !== value && handleChange(v)}
        disabled={busy}
      >
        <SelectTrigger className="h-8 w-44 text-sm">
          <SelectValue placeholder="Route for review…" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={UNASSIGNED}>Unassigned</SelectItem>
          {principals.map((p) => (
            <SelectItem key={p.id} value={p.id}>
              {p.label}
              {p.kind === "agent" ? " (agent)" : ""}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      {error && <span className="text-xs text-destructive">{error}</span>}
    </div>
  );
}
