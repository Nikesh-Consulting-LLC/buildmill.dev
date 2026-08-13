"use client";

// US-33.1 + US-33.3: what the factory costs, and the rates it is costed at.
//
// Nothing has ever written `runs.cost_usd` — the columns have been in the schema
// since migration 005 and every run reported null spend. The gateway now meters
// every model call, and every figure here is a query over those append-only
// rows: no counter exists to drift, because a drifted cost figure is worse than
// no cost figure — it will be believed.
//
// Tokens in and out stay separate everywhere. They have different prices, and
// collapsing them destroys the only information that explains why two runs with
// the same token count cost differently.

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";

import { apiCall } from "@/lib/api";
import { createClient } from "@/lib/supabase/client";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { toastError, toastSuccess } from "@/components/ui/toast";
import { cn } from "@/lib/utils";

type Row = {
  key: string | null;
  label: string;
  tokens_in: number;
  tokens_out: number;
  cache_read_tokens: number;
  cache_write_tokens: number;
  cost_usd: number | null;
  calls: number;
  unparsed_calls: number;
};

type Breakdown = {
  group_by: string;
  days: number;
  rows: Row[];
  totals: Omit<Row, "key" | "label">;
};

type Price = {
  model: string;
  input_per_mtok: number;
  output_per_mtok: number;
  cache_read_per_mtok: number | null;
  cache_write_per_mtok: number | null;
};

/** US-38.1: how much of the input side never had to be re-read.
 *
 * A cache read bills at a fraction of the input rate, so on a workload that
 * re-sends its conversation every turn this share IS the bill. Shown as a
 * share of input rather than a raw count, because the raw count is already two
 * columns to its left and the question here is "is caching working at all".
 *
 * A dash, not 0%, when nothing reported it: rows written before the split carry
 * NULL, and "we did not measure this" must not read as "nothing was cached". */
function cacheShare(row: {
  tokens_in: number;
  cache_read_tokens: number;
}): string {
  if (!row.tokens_in || !row.cache_read_tokens) return "—";
  return `${Math.round((row.cache_read_tokens / row.tokens_in) * 100)}%`;
}

const DIMENSIONS = [
  { key: "project", label: "By project" },
  { key: "agent", label: "By agent" },
  { key: "provider", label: "By provider" },
  { key: "model", label: "By model" },
];
const WINDOWS = [
  { days: 1, label: "Today" },
  { days: 7, label: "7 days" },
  { days: 30, label: "30 days" },
  { days: 90, label: "90 days" },
];

function money(value: number | null | undefined) {
  if (value == null) return "—";
  return `$${value.toFixed(value < 1 ? 4 : 2)}`;
}

function tokens(value: number) {
  return value.toLocaleString();
}

// US-9.7: `orgId` is resolved server-side (settings/spend/page.tsx via
// requireOrg) and this component is remounted with `key={orgId}` whenever
// the active workspace changes — so a stale org can never linger in state
// the way it would if this component re-derived and cached its own orgId.
export default function SpendView({ orgId }: { orgId: string }) {
  const [groupBy, setGroupBy] = useState("project");
  const [days, setDays] = useState(30);
  const [data, setData] = useState<Breakdown | null>(null);
  const [prices, setPrices] = useState<Price[]>([]);
  const [orgModels, setOrgModels] = useState<string[]>([]);
  // US-52.4: runs billed to a Claude subscription in this window. They bypass
  // the gateway, so they appear in NO figure on this page — a separate line,
  // never lumped into "could not be measured", which flags metering problems.
  const [subscriptionRuns, setSubscriptionRuns] = useState(0);
  const [error, setError] = useState<string | null>(null);

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
    const since = new Date(Date.now() - days * 86_400_000).toISOString();
    const { count: subCount } = await supabase
      .from("runs")
      .select("id", { count: "exact", head: true })
      .eq("org_id", orgId)
      .eq("billing", "subscription")
      .gte("created_at", since);
    setSubscriptionRuns(subCount ?? 0);
    try {
      setData(
        await apiCall(
          `/api/v1/llm/orgs/${orgId}/spend?group_by=${groupBy}&days=${days}`,
        ),
      );
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [orgId, groupBy, days]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="flex w-full flex-col gap-6">
      <div>
        <h2 className="text-lg font-semibold">Spend</h2>
        <p className="max-w-3xl text-sm text-muted-foreground">
          Every model call the factory makes passes through its own gateway, which
          meters it. These figures are queries over those records, not counters —
          and cost uses the rate that was in force when each call was made, so
          repricing a model changes what future runs cost and never rewrites what
          past ones did.
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        {DIMENSIONS.map((d) => (
          <Button
            key={d.key}
            size="sm"
            variant={groupBy === d.key ? "default" : "outline"}
            onClick={() => setGroupBy(d.key)}
          >
            {d.label}
          </Button>
        ))}
        <span className="ml-auto flex gap-2">
          {WINDOWS.map((w) => (
            <Button
              key={w.days}
              size="sm"
              variant={days === w.days ? "default" : "outline"}
              onClick={() => setDays(w.days)}
            >
              {w.label}
            </Button>
          ))}
        </span>
      </div>

      {error && <p className="text-sm text-destructive">{error}</p>}

      {data === null ? (
        <p className="text-sm text-muted-foreground">Loading spend…</p>
      ) : data.rows.length === 0 ? (
        <div className="rounded-md border border-dashed p-8 text-sm text-muted-foreground">
          <p className="font-medium text-foreground">
            Nothing metered in this window.
          </p>
          <p className="mt-2 max-w-2xl">
            Spend appears here as soon as an agent makes a model call through the
            factory gateway. Runs that predate metering report nothing rather than
            zero — the calls happened, they were simply never counted.
          </p>
        </div>
      ) : (
        <div className="overflow-x-auto rounded-md border">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b bg-muted/40 text-xs">
                <th className="px-3 py-2 font-medium">
                  {DIMENSIONS.find((d) => d.key === data.group_by)?.label.replace(
                    "By ",
                    "",
                  )}
                </th>
                <th className="px-3 py-2 text-right font-medium">Tokens in</th>
                <th className="px-3 py-2 text-right font-medium">Tokens out</th>
                {/* US-38.1: the share of input served from the provider's
                    prompt cache. Hidden on narrow screens rather than allowed
                    to overflow the table (us-35.7). */}
                <th className="hidden px-3 py-2 text-right font-medium lg:table-cell">
                  Cached
                </th>
                <th className="px-3 py-2 text-right font-medium">Cost</th>
                <th className="px-3 py-2 text-right font-medium">Calls</th>
              </tr>
            </thead>
            <tbody>
              {data.rows.map((r) => (
                <tr key={r.key ?? "unattributed"} className="border-b last:border-0">
                  <td className="px-3 py-1.5">
                    {r.label}
                    {/* US-33.3: what we could not measure, named rather than
                        quietly dropped from the total. */}
                    {r.unparsed_calls > 0 && (
                      <Badge
                        variant="outline"
                        className="ml-2 text-[11px] text-amber-700 dark:text-amber-400"
                        title="Calls whose provider usage could not be read. They are counted as calls but contribute no tokens or cost."
                      >
                        {r.unparsed_calls} unmeasured
                      </Badge>
                    )}
                  </td>
                  <td className="px-3 py-1.5 text-right font-mono text-xs">
                    {tokens(r.tokens_in)}
                  </td>
                  <td className="px-3 py-1.5 text-right font-mono text-xs">
                    {tokens(r.tokens_out)}
                  </td>
                  <td
                    className="hidden px-3 py-1.5 text-right font-mono text-xs lg:table-cell"
                    title={
                      r.cache_read_tokens
                        ? `${tokens(r.cache_read_tokens)} read from cache, ${tokens(
                            r.cache_write_tokens
                          )} written to it`
                        : "nothing reported a cache hit here"
                    }
                  >
                    {cacheShare(r)}
                  </td>
                  <td className="px-3 py-1.5 text-right font-mono text-xs">
                    {money(r.cost_usd)}
                  </td>
                  <td className="px-3 py-1.5 text-right font-mono text-xs">
                    {r.calls}
                  </td>
                </tr>
              ))}
              <tr className="bg-muted/30 font-medium">
                <td className="px-3 py-2">
                  Total
                  {data.totals.unparsed_calls > 0 && (
                    <span className="ml-2 text-xs font-normal text-amber-700 dark:text-amber-400">
                      · {data.totals.unparsed_calls} call(s) could not be measured
                    </span>
                  )}
                  {subscriptionRuns > 0 && (
                    <span className="ml-2 text-xs font-normal text-muted-foreground">
                      · {subscriptionRuns} run(s) on subscription (off-meter, by
                      design)
                    </span>
                  )}
                </td>
                <td className="px-3 py-2 text-right font-mono text-xs">
                  {tokens(data.totals.tokens_in)}
                </td>
                <td className="px-3 py-2 text-right font-mono text-xs">
                  {tokens(data.totals.tokens_out)}
                </td>
                <td className="hidden px-3 py-2 text-right font-mono text-xs lg:table-cell">
                  {cacheShare(data.totals)}
                </td>
                <td className="px-3 py-2 text-right font-mono text-xs">
                  {money(data.totals.cost_usd)}
                </td>
                <td className="px-3 py-2 text-right font-mono text-xs">
                  {data.totals.calls}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      )}

      <Prices
        orgId={orgId}
        prices={prices}
        orgModels={orgModels}
        onSaved={() => load()}
      />
    </div>
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
    <div className="grid gap-3 rounded-lg border p-4 text-sm">
      <span className="font-medium">Rates</span>
      <p className="max-w-3xl text-xs text-muted-foreground">
        Dollars per million tokens, as every provider quotes them. Tokens are the
        measured fact; money is tokens times these. A model with no rate is
        metered in tokens and reports its cost as unknown — deliberately, because
        &quot;we have no price for this&quot; and &quot;it was free&quot; must not
        read the same. Rates come from your{" "}
        <Link
          href="/settings/llm-providers"
          className="underline underline-offset-4"
        >
          provider&apos;s
        </Link>{" "}
        published pricing; the factory does not guess them.
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
          counted in tokens and cost nothing on this page until a rate is set.
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
