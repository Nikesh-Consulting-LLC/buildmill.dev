"use client";

import { useCallback, useEffect, useState } from "react";
import { createClient } from "@/lib/supabase/client";
import { HELP_DEFAULTS } from "./help-content";

/** US-2.30: everyone reads, only the superadmin writes. Overrides come
 * from the help_content_overrides RPC (help/* rows only); any failure —
 * offline, RPC missing, no session — silently renders the factory
 * defaults instead of breaking the page. */
export function useHelpText() {
  const [overrides, setOverrides] = useState<Record<string, string>>({});

  useEffect(() => {
    let cancelled = false;
    createClient()
      .rpc("help_content_overrides")
      .then(({ data }) => {
        if (cancelled || !data) return;
        setOverrides(
          Object.fromEntries(
            data.map((r: { prompt_key: string; content: string }) => [
              r.prompt_key,
              r.content,
            ])
          )
        );
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return useCallback(
    (key: string) => overrides[key] ?? HELP_DEFAULTS[key] ?? "",
    [overrides]
  );
}

export type HelpText = ReturnType<typeof useHelpText>;
