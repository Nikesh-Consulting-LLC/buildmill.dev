"use client";

import { useEffect, useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import { Loader2, TriangleAlert } from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import { apiFetch } from "@/lib/api";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { LlmProvider } from "./providers-section";

export type LlmRoute = {
  function_key: string;
  provider_id: string;
  model: string;
};

type RegistryFunction = {
  key: string;
  label: string;
  description: string;
};

const DEFAULT_VALUE = "__default__";

export function RoutingSection({
  orgId,
  providers,
  routes,
}: {
  orgId: string;
  providers: LlmProvider[];
  routes: LlmRoute[];
}) {
  const router = useRouter();
  const [registry, setRegistry] = useState<RegistryFunction[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const defaultProvider = providers.find((p) => p.is_default);
  const routeByKey = new Map(routes.map((r) => [r.function_key, r]));

  useEffect(() => {
    let cancelled = false;
    apiFetch("/api/v1/llm/functions")
      .then((data) => {
        if (!cancelled) setRegistry(data as RegistryFunction[]);
      })
      .catch((e: unknown) => {
        if (!cancelled)
          setLoadError(e instanceof Error ? e.message : "failed to load functions");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function setRoute(
    functionKey: string,
    providerId: string | null,
    model?: string
  ) {
    setError(null);
    setBusyKey(functionKey);
    const supabase = createClient();
    try {
      if (providerId === null) {
        const { error: deleteError } = await supabase
          .from("llm_function_routes")
          .delete()
          .eq("org_id", orgId)
          .eq("function_key", functionKey);
        if (deleteError) {
          setError(deleteError.message);
          return;
        }
      } else {
        const provider = providers.find((p) => p.id === providerId);
        const chosenModel = model ?? provider?.models[0];
        if (!chosenModel) {
          setError("That provider has no models to route to.");
          return;
        }
        const { error: upsertError } = await supabase
          .from("llm_function_routes")
          .upsert(
            {
              org_id: orgId,
              function_key: functionKey,
              provider_id: providerId,
              model: chosenModel,
            },
            { onConflict: "org_id,function_key" }
          );
        if (upsertError) {
          setError(upsertError.message);
          return;
        }
      }
      router.refresh();
    } finally {
      setBusyKey(null);
    }
  }

  if (loadError) {
    return (
      <p className="text-sm text-muted-foreground">
        Couldn&apos;t load the function list from the backend ({loadError}) —
        is the API running?
      </p>
    );
  }

  if (registry === null) {
    return (
      <p className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin" />
        Loading functions…
      </p>
    );
  }

  if (providers.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        Add a provider above first — every function needs at least the default
        to run on.
      </p>
    );
  }

  return (
    <div className="grid gap-3">
      <ul className="grid gap-2">
        {registry.map((fn) => {
          const route = routeByKey.get(fn.key);
          const routedProvider = route
            ? providers.find((p) => p.id === route.provider_id)
            : undefined;
          // A stale route (model removed from the provider's list) still
          // resolves to the default at call time — say so.
          const stale =
            route && routedProvider && !routedProvider.models.includes(route.model);
          const providerValue = route ? route.provider_id : DEFAULT_VALUE;
          const providerItems = [
            {
              value: DEFAULT_VALUE,
              label: defaultProvider
                ? `Default (${defaultProvider.name} · ${defaultProvider.default_model})`
                : "Default",
            },
            ...providers.map((p) => ({ value: p.id, label: p.name })),
          ];
          const modelItems = (routedProvider?.models ?? []).map((m) => ({
            value: m,
            label: m,
          }));
          if (stale && route) {
            modelItems.unshift({
              value: route.model,
              label: `${route.model} (removed)`,
            });
          }

          return (
            <li
              key={fn.key}
              className="flex flex-wrap items-center justify-between gap-3 rounded-md border px-3 py-2 text-sm"
            >
              <span className="flex min-w-0 flex-col">
                <span className="font-medium">{fn.label}</span>
                <span className="text-xs text-muted-foreground">
                  {fn.description}
                </span>
                {stale && (
                  <span className="mt-0.5 flex items-center gap-1 text-xs text-amber-600 dark:text-amber-400">
                    <TriangleAlert className="size-3" />
                    Model no longer on {routedProvider?.name} — falls back to
                    default
                  </span>
                )}
              </span>
              <span className="flex shrink-0 items-center gap-2">
                {busyKey === fn.key && (
                  <Loader2 className="size-4 animate-spin text-muted-foreground" />
                )}
                <Select
                  items={providerItems}
                  value={providerValue}
                  onValueChange={(v) => {
                    if (typeof v !== "string") return;
                    if (v === DEFAULT_VALUE) void setRoute(fn.key, null);
                    else void setRoute(fn.key, v);
                  }}
                >
                  <SelectTrigger
                    className="w-44"
                    aria-label={`${fn.label} provider`}
                    disabled={busyKey === fn.key}
                  >
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {providerItems.map((item) => (
                      <SelectItem key={item.value} value={item.value}>
                        {item.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                {route && routedProvider && (
                  <Select
                    items={modelItems}
                    value={route.model}
                    onValueChange={(v) => {
                      if (typeof v === "string")
                        void setRoute(fn.key, route.provider_id, v);
                    }}
                  >
                    <SelectTrigger
                      className="w-52"
                      aria-label={`${fn.label} model`}
                      disabled={busyKey === fn.key}
                    >
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {modelItems.map((item) => (
                        <SelectItem key={item.value} value={item.value}>
                          {item.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                )}
              </span>
            </li>
          );
        })}
      </ul>
      <p className="text-xs text-muted-foreground">
        Functions without a mapping use the default provider. If a routed call
        fails, it is retried once on the default before surfacing an error.
      </p>
      {error && <p className="text-sm font-medium text-destructive">{error}</p>}
    </div>
  );
}
