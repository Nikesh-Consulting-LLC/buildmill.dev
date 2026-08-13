"use client";

import { useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import { Bot, Loader2 } from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import { Button } from "@/components/ui/button";
import { confirmDialog } from "@/components/ui/confirm-dialog";

/** US-13.13: the per-deployment "agent may deploy" opt-in — the only way
 * a production deployment can be dispatched to an agent. Direct
 * Supabase write; the flip is recorded by the deployment config audit
 * trigger (area "agent-dispatch"). */
export function AgentDeployFlag({
  deploymentId,
  enabled,
}: {
  deploymentId: string;
  enabled: boolean;
}) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function flip() {
    if (
      !enabled &&
      !(await confirmDialog({
        title: "Allow agent dispatch to production?",
        description:
          "An agent granted the deploy capability will be able to trigger " +
          "this production deployment when you dispatch it. Protected " +
          "deployments still refuse agents regardless. The flip is audited.",
        confirmLabel: "Allow",
      }))
    )
      return;
    setBusy(true);
    setError(null);
    const supabase = createClient();
    const { error: dbError } = await supabase
      .from("deployments")
      .update({ agent_dispatch_allowed: !enabled })
      .eq("id", deploymentId);
    if (dbError) setError(dbError.message);
    else router.refresh();
    setBusy(false);
  }

  return (
    <span className="flex items-center gap-1.5">
      <Button
        variant="outline"
        size="sm"
        className="h-7 gap-1 px-2 text-xs"
        disabled={busy}
        onClick={flip}
        title="US-13.13: production deployments refuse agent dispatch unless a human sets this (audited)."
      >
        {busy ? (
          <Loader2 className="size-3 animate-spin" />
        ) : (
          <Bot className="size-3" />
        )}
        {enabled ? "Agent dispatch: allowed" : "Agent dispatch: off"}
      </Button>
      {error && <span className="text-xs text-destructive">{error}</span>}
    </span>
  );
}
