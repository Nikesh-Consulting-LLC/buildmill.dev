"use client";

import { useEffect, useState } from "react";
import { CheckCircle2, Loader2, XCircle } from "lucide-react";
import { apiFetch } from "@/lib/api";

type Me = { user_id: string; email: string; org_id: string };

export function ApiStatus() {
  const [state, setState] = useState<
    | { kind: "loading" }
    | { kind: "ok"; me: Me }
    | { kind: "error"; message: string }
  >({ kind: "loading" });

  useEffect(() => {
    apiFetch("/api/v1/auth/me")
      .then((me: Me) => setState({ kind: "ok", me }))
      .catch((e: Error) => setState({ kind: "error", message: e.message }));
  }, []);

  if (state.kind === "loading") {
    return (
      <p className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin" />
        Checking backend…
      </p>
    );
  }

  if (state.kind === "error") {
    return (
      <p className="flex items-center gap-2 text-sm text-muted-foreground">
        <XCircle className="size-4 text-destructive" />
        Backend not reachable ({state.message}). Start it with{" "}
        <code className="rounded bg-muted px-1 font-mono text-xs">
          npm run api
        </code>
      </p>
    );
  }

  return (
    <p className="flex items-center gap-2 text-sm">
      <CheckCircle2 className="size-4 text-emerald-600 dark:text-emerald-400" />
      Connected as {state.me.email}
      <span className="font-mono text-xs text-muted-foreground">
        org {state.me.org_id.slice(0, 8)}…
      </span>
    </p>
  );
}
