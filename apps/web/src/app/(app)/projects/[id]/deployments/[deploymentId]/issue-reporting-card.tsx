"use client";

import { useState } from "react";
import { Check, Copy, Eye, KeyRound, RefreshCw } from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import { API_URL } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Label } from "@/components/ui/label";

const KEY_PLACEHOLDER = "<your-report-key>";

/** The two embed snippets, built from one source so the id, the key and the
 *  endpoint can never drift apart between them. US-16.4/16.5 serve the
 *  scripts; this is what a manager copies. */
function snippets(deploymentId: string, key: string, webOrigin: string) {
  const endpoint = `${API_URL}/api/v1/report`;
  const attrs = `data-deployment="${deploymentId}" data-key="${key}" data-endpoint="${endpoint}"`;
  return [
    {
      id: "sdk",
      label: "Automatic crash reporting",
      hint: "Paste in the app's <head>. Unhandled errors report themselves.",
      text: `<script src="${webOrigin}/embed/report-sdk.js" ${attrs} async></script>`,
    },
    {
      id: "widget",
      label: "Report-an-issue widget",
      hint: "Adds a small trigger your app's own users can submit through.",
      text: `<script src="${webOrigin}/embed/report-widget.js" ${attrs} async></script>`,
    },
  ];
}

function CopyBlock({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="flex items-start gap-2">
      <pre className="min-w-0 flex-1 overflow-x-auto rounded-md border bg-muted px-3 py-2 font-mono text-xs whitespace-pre-wrap">
        {text}
      </pre>
      <Button
        variant="outline"
        size="sm"
        onClick={async () => {
          await navigator.clipboard.writeText(text);
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        }}
        aria-label="Copy snippet"
      >
        {copied ? (
          <Check className="size-4 text-green-600" />
        ) : (
          <Copy className="size-4" />
        )}
      </Button>
    </div>
  );
}

/**
 * US-16.3: turn reporting on for this deployment and walk away with a snippet
 * that already has the id and the key in it.
 *
 * Scoped per deployment on purpose — a project's UAT and Production hold
 * different keys, so a manager can tell which environment is on fire. The key
 * is hidden until asked for: it is revealable (US-16.1), but rendering it into
 * the page for everyone who opens the deployment is not the same thing.
 */
export function IssueReportingCard({
  orgId,
  deploymentId,
  initialEnabled,
  initialLast4,
  initialSelfMonitoring,
}: {
  orgId: string;
  deploymentId: string;
  initialEnabled: boolean;
  initialLast4: string | null;
  initialSelfMonitoring: boolean;
}) {
  const [enabled, setEnabled] = useState(initialEnabled);
  const [selfMonitoring, setSelfMonitoring] = useState(initialSelfMonitoring);
  const [last4, setLast4] = useState<string | null>(initialLast4);
  const [key, setKey] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const webOrigin =
    typeof window === "undefined" ? "" : window.location.origin;

  async function mintKey() {
    const supabase = createClient();
    const { data, error: rpcError } = await supabase.rpc(
      "generate_deployment_report_key",
      { p_deployment: deploymentId },
    );
    if (rpcError) throw new Error(rpcError.message);
    const minted = typeof data === "string" ? data : null;
    setKey(minted);
    setLast4(minted ? minted.slice(-4) : null);
  }

  async function toggle(next: boolean) {
    setBusy(true);
    setError(null);
    try {
      // A first enable needs a key to exist before the toggle means anything.
      if (next && !last4) await mintKey();
      const { error: dbError } = await createClient()
        .from("deployments")
        .update({ issue_reporting_enabled: next })
        .eq("id", deploymentId)
        .eq("org_id", orgId);
      if (dbError) throw new Error(dbError.message);
      setEnabled(next);
      // Disabling is a kill switch, not a revocation: the key survives, so
      // re-enabling does not mean redeploying every app that carries it.
      if (!next) setKey(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function setSelf(next: boolean) {
    setBusy(true);
    setError(null);
    const { error: dbError } = await createClient()
      .from("deployments")
      .update({ is_self_monitoring: next })
      .eq("id", deploymentId)
      .eq("org_id", orgId);
    setBusy(false);
    if (dbError) {
      // The one-per-org unique index speaks for itself here.
      setError(
        dbError.code === "23505"
          ? "Another deployment in this org is already flagged as Build Mill itself."
          : dbError.message,
      );
      return;
    }
    setSelfMonitoring(next);
  }

  async function reveal() {
    setBusy(true);
    setError(null);
    const { data, error: rpcError } = await createClient().rpc(
      "reveal_deployment_report_key",
      { p_deployment: deploymentId },
    );
    if (rpcError) setError(rpcError.message);
    else if (typeof data === "string") setKey(data);
    setBusy(false);
  }

  async function rotate() {
    if (
      !window.confirm(
        "Rotate this deployment's report key?\n\nThe current key stops working immediately — any app still carrying it will silently stop reporting until you redeploy it with the new one.",
      )
    )
      return;
    setBusy(true);
    setError(null);
    try {
      await mintKey();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Issue reporting</CardTitle>
        <CardDescription>
          Let this deployment report its own crashes, and its users report
          problems, straight into the factory. Each deployment carries its own
          key, so UAT and Production stay apart.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        <Label className="flex cursor-pointer items-center gap-2 text-sm font-normal">
          <input
            type="checkbox"
            className="size-4 accent-primary"
            checked={enabled}
            disabled={busy}
            onChange={(e) => toggle(e.target.checked)}
          />
          Accept reports from this deployment
        </Label>

        {enabled && (
          <>
            <div className="flex flex-wrap items-center gap-2">
              <KeyRound className="size-4 text-muted-foreground" />
              <code className="rounded-md border bg-muted px-3 py-1.5 font-mono text-xs">
                {key ?? (last4 ? `Key set · ···· ${last4}` : "No key yet")}
              </code>
              {!key && (
                <Button
                  variant="outline"
                  size="sm"
                  disabled={busy}
                  onClick={reveal}
                >
                  <Eye className="mr-1 size-4" /> Show
                </Button>
              )}
              <Button
                variant="outline"
                size="sm"
                disabled={busy}
                onClick={rotate}
              >
                <RefreshCw className="mr-1 size-4" /> Rotate key
              </Button>
            </div>

            <div className="grid gap-3">
              {snippets(deploymentId, key ?? KEY_PLACEHOLDER, webOrigin).map(
                (s) => (
                  <div key={s.id} className="grid gap-1">
                    <p className="text-sm font-medium">{s.label}</p>
                    <p className="text-xs text-muted-foreground">{s.hint}</p>
                    <CopyBlock text={s.text} />
                  </div>
                ),
              )}
              {!key && (
                <p className="text-xs text-muted-foreground">
                  Press <strong>Show</strong> to substitute the real key into
                  both snippets before copying.
                </p>
              )}
            </div>
          </>
        )}

        {/* US-16.8: which deployment *is* Build Mill. One per org (the database
            enforces it), and it is what makes the factory's own errors visible
            on the superadmin console rather than only in this org's Reports. */}
        <Label className="flex cursor-pointer items-start gap-2 border-t pt-3 text-sm font-normal">
          <input
            type="checkbox"
            className="mt-0.5 size-4 accent-primary"
            checked={selfMonitoring}
            disabled={busy}
            onChange={(e) => setSelf(e.target.checked)}
          />
          <span>
            This deployment is Build Mill itself
            <span className="block text-xs text-muted-foreground">
              Reports filed against it are the factory&rsquo;s own errors, and
              appear on the superadmin System issues console. Only one
              deployment per org can carry this.
            </span>
          </span>
        </Label>

        {error && (
          <p className="text-sm font-medium text-destructive">{error}</p>
        )}
      </CardContent>
    </Card>
  );
}
