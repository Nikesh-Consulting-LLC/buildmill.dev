"use client";

// US-33.1 + US-36.4 + US-38.1: the model rates — moved here from the old
// Settings → Spend page by us-95.1. Rates are configuration (the provider's
// published pricing, hand-entered), so they live in Settings beside the
// providers they price; the spend REPORT they feed moved to the top-level
// Costs section.
//
// Self-contained: loads its own prices and model list, reloads after a save.

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";

import { apiCall } from "@/lib/api";
import { createClient } from "@/lib/supabase/client";
import { Button } from "@/components/ui/button";
import { toastError, toastSuccess } from "@/components/ui/toast";
import { cn } from "@/lib/utils";

type Price = {
  model: string;
  input_per_mtok: number;
  output_per_mtok: number;
  cache_read_per_mtok: number | null;
  cache_write_per_mtok: number | null;
};

export function RatesSection({ orgId }: { orgId: string }) {
  const [prices, setPrices] = useState<Price[]>([]);
  const [orgModels, setOrgModels] = useState<string[]>([]);

  const load = useCallback(async () => {
    const supabase = createClient();
    const [priceRows, providerRows] = await Promise.all([
      supabase
        .from("llm_model_prices")
        .select(
          "model, input_per_mtok, output_per_mtok, cache_read_per_mtok, cache_write_per_mtok"
        )
        .eq("org_id", orgId)
        .order("model", { ascending: true }),
      supabase.from("llm_providers").select("models, default_model").eq("org_id", orgId),
    ]);
    setPrices(
      ((priceRows.data ?? []) as unknown as Price[]).map((p) => ({
        model: p.model,
        input_per_mtok: Number(p.input_per_mtok),
        output_per_mtok: Number(p.output_per_mtok),
        // US-38.1: null stays null. It means "charge these at the input
        // rate", which is what they have always been charged at — coercing
        // it to 0 would make every cached token free.
        cache_read_per_mtok:
          p.cache_read_per_mtok == null ? null : Number(p.cache_read_per_mtok),
        cache_write_per_mtok:
          p.cache_write_per_mtok == null
            ? null
            : Number(p.cache_write_per_mtok),
      })),
    );
    const models = new Set<string>();
    for (const row of providerRows.data ?? []) {
      for (const m of (row.models as string[] | null) ?? []) models.add(m);
      if (row.default_model) models.add(row.default_model as string);
    }
    setOrgModels([...models].sort());
  }, [orgId]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <Prices orgId={orgId} prices={prices} orgModels={orgModels} onSaved={() => load()} />
  );
}

function Prices({
  orgId,
  prices,
  orgModels,
  onSaved,
}: {
  orgId: string;
  prices: Price[];
  orgModels: string[];
  onSaved: () => void;
}) {
  const [model, setModel] = useState("");
  const [input, setInput] = useState("");
  const [output, setOutput] = useState("");
  // US-38.1: the two cache rates. Blank is meaningful and is the default --
  // "charge these at the input rate", which is what they have always been
  // charged at. They MUST round-trip through this form: the endpoint upserts
  // every column, so a form that did not carry them would silently wipe a rate
  // the manager had set the moment they edited the input price.
  const [cacheRead, setCacheRead] = useState("");
  const [cacheWrite, setCacheWrite] = useState("");
  const [busy, setBusy] = useState(false);

  // US-36.4: the endpoint always upserted, so changing a rate was possible all
  // along — nothing on screen said so, and the fields never prefilled, so a
  // manager who guessed still retyped both numbers blind. Knowing which model
  // already has a rate is what lets the form say whether it is adding or
  // replacing, and show what it is replacing.
  const existing = prices.find((p) => p.model === model) ?? null;

  function startEdit(p: Price) {
    setModel(p.model);
    setInput(String(p.input_per_mtok));
    setOutput(String(p.output_per_mtok));
    setCacheRead(p.cache_read_per_mtok == null ? "" : String(p.cache_read_per_mtok));
    setCacheWrite(
      p.cache_write_per_mtok == null ? "" : String(p.cache_write_per_mtok)
    );
  }

  function chooseModel(next: string) {
    setModel(next);
    // Prefill from the current rate so the dropdown path is not blind entry
    // either; a model with no rate starts empty.
    const current = prices.find((p) => p.model === next);
    setInput(current ? String(current.input_per_mtok) : "");
    setOutput(current ? String(current.output_per_mtok) : "");
    setCacheRead(
      current?.cache_read_per_mtok == null ? "" : String(current.cache_read_per_mtok)
    );
    setCacheWrite(
      current?.cache_write_per_mtok == null
        ? ""
        : String(current.cache_write_per_mtok)
    );
  }

  function clearForm() {
    setModel("");
    setInput("");
    setOutput("");
    setCacheRead("");
    setCacheWrite("");
  }

  async function save(m: string, i: number, o: number) {
    setBusy(true);
    try {
      await apiCall(`/api/v1/llm/orgs/${orgId}/model-prices`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          model: m,
          input_per_mtok: i,
          output_per_mtok: o,
          // Blank means "no separate rate" -- null, not 0. A 0 here would make
          // every cached token free, which is the one reading us-33.1 forbids.
          cache_read_per_mtok: cacheRead === "" ? null : Number(cacheRead),
          cache_write_per_mtok: cacheWrite === "" ? null : Number(cacheWrite),
        }),
      });
      toastSuccess(
        "Rate saved",
        "It applies to calls from now on. Calls already metered keep the rate they were charged at.",
      );
      clearForm();
      onSaved();
    } catch (e) {
      toastError("Could not save", (e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const unpriced = orgModels.filter((m) => !prices.some((p) => p.model === m));

  // A rate can outlive the provider config that introduced its model. Editing
  // one of those has to be possible too, or the dropdown silently blanks out
  // the row you just pressed Edit on.
  const selectableModels = [
    ...orgModels,
    ...prices.map((p) => p.model).filter((m) => !orgModels.includes(m)),
  ];

  return (
    <div className="grid gap-3 text-sm">
      <p className="max-w-3xl text-xs text-muted-foreground">
        Dollars per million tokens, as every provider quotes them. Tokens are the
        measured fact; money is tokens times these. A model with no rate is
        metered in tokens and reports its cost as unknown — deliberately, because
        &quot;we have no price for this&quot; and &quot;it was free&quot; must not
        read the same. Rates feed the{" "}
        <Link href="/costs" className="underline underline-offset-4">
          Costs
        </Link>{" "}
        section; they come from your provider&apos;s published pricing — the
        factory does not guess them.
      </p>

      {prices.length > 0 && (
        <div className="overflow-x-auto rounded-md border">
          <table className="w-full text-left text-xs">
            <thead>
              <tr className="border-b bg-muted/40">
                <th className="px-3 py-1.5 font-medium">Model</th>
                <th className="px-3 py-1.5 text-right font-medium">In / Mtok</th>
                <th className="px-3 py-1.5 text-right font-medium">Out / Mtok</th>
                {/* US-38.1: input is sold in three classes, so it is priced in
                    three. Blank means "same as input", which is how every rate
                    behaved before the split. */}
                <th className="hidden px-3 py-1.5 text-right font-medium sm:table-cell">
                  Cache rd
                </th>
                <th className="hidden px-3 py-1.5 text-right font-medium sm:table-cell">
                  Cache wr
                </th>
                {/* US-36.4: the row is where a manager looks to correct a
                    rate, so that is where the action belongs. */}
                <th className="w-16 px-3 py-1.5 text-right font-medium" />
              </tr>
            </thead>
            <tbody>
              {prices.map((p) => (
                <tr
                  key={p.model}
                  className={cn(
                    "border-b last:border-0",
                    model === p.model && "bg-muted/30"
                  )}
                >
                  <td className="px-3 py-1.5 font-mono">{p.model}</td>
                  <td className="px-3 py-1.5 text-right font-mono">
                    ${p.input_per_mtok.toFixed(2)}
                  </td>
                  <td className="px-3 py-1.5 text-right font-mono">
                    ${p.output_per_mtok.toFixed(2)}
                  </td>
                  <td
                    className="hidden px-3 py-1.5 text-right font-mono text-muted-foreground sm:table-cell"
                    title="Blank charges cache reads at the input rate."
                  >
                    {p.cache_read_per_mtok == null
                      ? "= in"
                      : `$${p.cache_read_per_mtok.toFixed(2)}`}
                  </td>
                  <td
                    className="hidden px-3 py-1.5 text-right font-mono text-muted-foreground sm:table-cell"
                    title="Blank charges cache writes at the input rate."
                  >
                    {p.cache_write_per_mtok == null
                      ? "= in"
                      : `$${p.cache_write_per_mtok.toFixed(2)}`}
                  </td>
                  <td className="px-3 py-1.5 text-right">
                    <button
                      type="button"
                      onClick={() => startEdit(p)}
                      className="text-xs underline underline-offset-4 hover:text-foreground"
                    >
                      Edit
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {unpriced.length > 0 && (
        <p className="text-xs text-amber-700 dark:text-amber-400">
          No rate yet for: {unpriced.join(", ")}. Calls on those models are
          counted in tokens and cost nothing on the Costs page until a rate is
          set.
        </p>
      )}

      {/* US-36.4: the form used to sit directly under the "no rate yet" warning
          with a "Set rate" button, so it read as add-only and the fact that the
          endpoint upserts was invisible. It now says which of the two it is
          doing, and what it is about to replace. */}
      <div className="grid gap-1 border-t pt-3">
        <span className="text-xs font-medium">
          {existing ? `Update the rate for ${existing.model}` : "Set a rate"}
        </span>
        {existing ? (
          <p className="text-xs text-muted-foreground">
            Currently ${existing.input_per_mtok.toFixed(2)} in /{" "}
            ${existing.output_per_mtok.toFixed(2)} out per million tokens.
            Saving replaces it for calls from now on.
          </p>
        ) : (
          <p className="text-xs text-muted-foreground">
            Choose a model to set its rate, or press Edit on a row above to
            change one you already have.
          </p>
        )}
      </div>

      <div className="grid gap-2 sm:grid-cols-2 md:grid-cols-3 md:items-end">
        <label className="grid gap-1">
          <span className="text-xs text-muted-foreground">Model</span>
          <select
            value={model}
            onChange={(e) => chooseModel(e.target.value)}
            className="rounded-md border bg-background px-2 py-1"
          >
            <option value="">Choose a model…</option>
            {selectableModels.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        </label>
        <label className="grid gap-1">
          <span className="text-xs text-muted-foreground">Input $ / Mtok</span>
          <input
            type="number"
            min={0}
            step={0.01}
            value={input}
            autoComplete="off"
            onChange={(e) => setInput(e.target.value)}
            className="rounded-md border bg-background px-2 py-1"
          />
        </label>
        <label className="grid gap-1">
          <span className="text-xs text-muted-foreground">Output $ / Mtok</span>
          <input
            type="number"
            min={0}
            step={0.01}
            value={output}
            autoComplete="off"
            onChange={(e) => setOutput(e.target.value)}
            className="rounded-md border bg-background px-2 py-1"
          />
        </label>
        <label className="grid gap-1">
          <span className="text-xs text-muted-foreground">
            Cache read $ / Mtok
          </span>
          <input
            type="number"
            min={0}
            step={0.01}
            value={cacheRead}
            autoComplete="off"
            onChange={(e) => setCacheRead(e.target.value)}
            placeholder="same as input"
            className="rounded-md border bg-background px-2 py-1"
          />
        </label>
        <label className="grid gap-1">
          <span className="text-xs text-muted-foreground">
            Cache write $ / Mtok
          </span>
          <input
            type="number"
            min={0}
            step={0.01}
            value={cacheWrite}
            autoComplete="off"
            onChange={(e) => setCacheWrite(e.target.value)}
            placeholder="same as input"
            className="rounded-md border bg-background px-2 py-1"
          />
        </label>
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            disabled={busy || !model || input === "" || output === ""}
            onClick={() => void save(model, Number(input), Number(output))}
          >
            {busy ? "Saving…" : existing ? "Update rate" : "Set rate"}
          </Button>
          {model && !busy && (
            <Button size="sm" variant="ghost" onClick={clearForm}>
              Cancel
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
