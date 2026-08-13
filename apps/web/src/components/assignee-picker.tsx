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

// US-9.9: assign a work item to a principal (person or agent). Self-contained —
// loads the org's principals and the caller's manage_work capability itself, so
// it drops onto any work-item surface with just the issue + org id. The
// database (guard_issue_assignee trigger) is authoritative on who may assign.
export function AssigneePicker({
  issueId,
  orgId,
  assigneeId,
}: {
  issueId: string;
  orgId: string;
  assigneeId: string | null;
}) {
  const router = useRouter();
  const [principals, setPrincipals] = useState<Principal[]>([]);
  const [canAssign, setCanAssign] = useState(false);
  const [value, setValue] = useState(assigneeId ?? UNASSIGNED);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      const supabase = createClient();
      const {
        data: { user },
      } = await supabase.auth.getUser();

      const [{ data: members }, membership] = await Promise.all([
        supabase
          .from("organization_members")
          .select("principal_id, principals(kind, email, display_name)")
          .eq("org_id", orgId)
          .eq("status", "active"),
        user
          ? supabase
              .from("organization_members")
              .select("role")
              .eq("org_id", orgId)
              .eq("user_id", user.id)
              .maybeSingle()
          : Promise.resolve({ data: null }),
      ]);

      const rows = (members ?? []) as unknown as Array<{
        principal_id: string;
        principals: { kind: "human" | "agent"; email: string | null; display_name: string | null } | null;
      }>;
      setPrincipals(
        rows.map((m) => ({
          id: m.principal_id,
          kind: m.principals?.kind ?? "human",
          label: m.principals?.display_name || m.principals?.email || "Member",
        })),
      );

      const role = (membership?.data as { role?: string } | null)?.role;
      if (role) {
        const { data: caps } = await supabase
          .from("role_capabilities")
          .select("allowed")
          .eq("role", role)
          .eq("capability", "manage_work")
          .maybeSingle();
        setCanAssign(!!caps?.allowed);
      }
    })();
  }, [orgId]);

  async function handleChange(next: string) {
    setValue(next);
    setBusy(true);
    setError(null);
    const supabase = createClient();
    const { error: dbError } = await supabase
      .from("issues")
      .update({ assignee_id: next === UNASSIGNED ? null : next })
      .eq("id", issueId);
    if (dbError) {
      setError(dbError.message);
      setValue(assigneeId ?? UNASSIGNED);
    } else {
      router.refresh();
    }
    setBusy(false);
  }

  const current = principals.find((p) => p.id === value);

  if (!canAssign) {
    if (value === UNASSIGNED) return null;
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium text-muted-foreground">
        {current?.kind === "agent" ? <Bot className="size-3.5" /> : <User className="size-3.5" />}
        {current?.label ?? "Automatic"}
      </span>
    );
  }

  return (
    <span className="inline-flex items-center gap-1.5">
      <Select
        items={[
          { value: UNASSIGNED, label: "Automatic" },
          ...principals.map((p) => ({ value: p.id, label: p.label })),
        ]}
        value={value}
        onValueChange={(v) => typeof v === "string" && v !== value && handleChange(v)}
        disabled={busy}
      >
        <SelectTrigger
          className="h-7 w-40 text-xs"
          title="Automatic — the first capable agent claims it. Pick a principal to give it an owner."
        >
          <SelectValue placeholder="Assign…" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={UNASSIGNED}>Automatic</SelectItem>
          {principals.map((p) => (
            <SelectItem key={p.id} value={p.id}>
              {p.label}
              {p.kind === "agent" ? " (agent)" : ""}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      {error && <span className="text-xs text-destructive">{error}</span>}
    </span>
  );
}
