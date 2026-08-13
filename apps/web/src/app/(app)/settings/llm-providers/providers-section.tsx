"use client";

import { confirmDialog } from "@/components/ui/confirm-dialog";

import { useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import {
  BrainCircuit,
  KeyRound,
  Loader2,
  Pencil,
  Plus,
  Star,
  Trash2,
  X,
} from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { EmptyState } from "@/components/empty-state";

export type LlmProvider = {
  id: string;
  name: string;
  provider_type: string;
  base_url: string | null;
  models: string[];
  is_default: boolean;
  default_model: string | null;
  key_last4: string | null;
};

export const PROVIDER_TYPES = [
  { value: "anthropic", label: "Anthropic" },
  { value: "openai", label: "OpenAI" },
  { value: "google", label: "Google Gemini" },
  { value: "groq", label: "Groq" },
  { value: "xai", label: "xAI" },
  { value: "ollama", label: "Ollama (local)" },
];

const MODEL_SUGGESTIONS: Record<string, string[]> = {
  anthropic: ["claude-sonnet-5", "claude-opus-4-8", "claude-haiku-4-5"],
  openai: ["gpt-5.1", "gpt-4o", "gpt-4o-mini"],
  google: ["gemini-2.5-pro", "gemini-2.5-flash"],
  groq: ["llama-3.3-70b-versatile", "openai/gpt-oss-120b"],
  xai: ["grok-4", "grok-3", "grok-code-fast-1"],
  ollama: ["llama3.3", "qwen2.5-coder", "gpt-oss:20b"],
};

export function providerTypeLabel(type: string) {
  return PROVIDER_TYPES.find((p) => p.value === type)?.label ?? type;
}

type FormState = {
  id: string | null; // null = create
  name: string;
  providerType: string;
  baseUrl: string;
  models: string[];
  apiKey: string;
};

const BLANK_FORM: FormState = {
  id: null,
  name: "",
  providerType: "anthropic",
  baseUrl: "",
  models: [],
  apiKey: "",
};

export function ProvidersSection({
  orgId,
  providers,
}: {
  orgId: string;
  providers: LlmProvider[];
}) {
  const router = useRouter();
  const [form, setForm] = useState<FormState | null>(null);
  const [newModel, setNewModel] = useState("");
  const [saving, setSaving] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // "Make default" needs a model choice for the fallback target.
  const [defaultPick, setDefaultPick] = useState<{
    provider: LlmProvider;
    model: string;
  } | null>(null);

  const editing = form?.id ? providers.find((p) => p.id === form.id) : null;
  const isOllama = form?.providerType === "ollama";
  const suggestions = form ? MODEL_SUGGESTIONS[form.providerType] ?? [] : [];

  function openCreate() {
    setError(null);
    setNewModel("");
    setForm({ ...BLANK_FORM });
  }

  function openEdit(p: LlmProvider) {
    setError(null);
    setNewModel("");
    setForm({
      id: p.id,
      name: p.name,
      providerType: p.provider_type,
      baseUrl: p.base_url ?? "",
      models: [...p.models],
      apiKey: "",
    });
  }

  /** US-31.4: the tail of an email/hostname is not the tail of an API key.
   * The same autofill that put an email in the model list also saved one AS
   * the key — write-only, so `Key set · ends in ····.llc` was the only
   * evidence. Flag it; only the manager can re-enter the real key. */
  function keyLooksWrong(last4: string | null | undefined): boolean {
    if (!last4) return false;
    return last4.includes("@") || /^\.[a-z]{2,}$/i.test(last4);
  }

  /** US-27.8: a model id is an id. This org's Anthropic provider had
   * `kaushlesh@nikesh.llc` in its model list, offered in every route dropdown
   * — and since US-27.8 the gateway resolves a provider BY model, so a
   * nonsense entry is no longer merely untidy. */
  function modelIdProblem(m: string): string | null {
    if (/\s/.test(m)) return "A model id has no spaces.";
    if (m.includes("@")) return "That looks like an email address, not a model id.";
    return null;
  }

  function addModel() {
    if (!form) return;
    const m = newModel.trim();
    if (!m || form.models.includes(m)) return;
    const problem = modelIdProblem(m);
    if (problem) {
      setError(problem);
      return;
    }
    setError(null);
    setForm({ ...form, models: [...form.models, m] });
    setNewModel("");
  }

  function removeModel(m: string) {
    if (!form) return;
    setForm({ ...form, models: form.models.filter((x) => x !== m) });
  }

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    if (!form) return;
    setError(null);

    // An unadded model id left in the input still counts — nobody should
    // lose a model to a missed "Add" click.
    const models = [...form.models];
    const pending = newModel.trim();
    if (pending && !models.includes(pending)) models.push(pending);

    if (!form.name.trim()) {
      setError("Name is required.");
      return;
    }
    if (models.length === 0) {
      setError("Add at least one model.");
      return;
    }
    const badModel = models.map((m) => [m, modelIdProblem(m)] as const).find(
      ([, problem]) => problem
    );
    if (badModel) {
      setError(`"${badModel[0]}" — ${badModel[1]}`);
      return;
    }
    if (isOllama && !form.baseUrl.trim()) {
      setError("Base URL is required for Ollama.");
      return;
    }
    // US-31.4: an API key contains no whitespace and no '@' — refuse before
    // anything is saved, so an autofilled email never reaches Vault. The
    // set_llm_provider_key RPC enforces the same rule server-side.
    const key = form.apiKey.trim();
    if (key && (/\s/.test(key) || key.includes("@"))) {
      setError(
        key.includes("@")
          ? "That looks like an email address, not an API key — check for browser autofill."
          : "An API key has no spaces — check what was pasted."
      );
      return;
    }

    setSaving(true);
    const supabase = createClient();
    try {
      let providerId = form.id;
      if (providerId) {
        const current = providers.find((p) => p.id === providerId);
        // Keep the fallback target valid when its model was removed.
        const patch: Record<string, unknown> = {
          name: form.name.trim(),
          base_url: isOllama ? form.baseUrl.trim() : null,
          models,
        };
        if (
          current?.is_default &&
          current.default_model &&
          !models.includes(current.default_model)
        ) {
          patch.default_model = models[0];
        }
        const { error: updateError } = await supabase
          .from("llm_providers")
          .update(patch)
          .eq("id", providerId)
          .eq("org_id", orgId);
        if (updateError) {
          setError(updateError.message);
          return;
        }
      } else {
        // The org's first provider becomes the default automatically.
        const isFirst = providers.length === 0;
        const { data, error: insertError } = await supabase
          .from("llm_providers")
          .insert({
            org_id: orgId,
            name: form.name.trim(),
            provider_type: form.providerType,
            base_url: isOllama ? form.baseUrl.trim() : null,
            models,
            is_default: isFirst,
            default_model: isFirst ? models[0] : null,
          })
          .select("id")
          .single();
        if (insertError) {
          setError(insertError.message);
          return;
        }
        providerId = data.id;
      }

      if (form.apiKey.trim() && providerId) {
        const { error: keyError } = await supabase.rpc("set_llm_provider_key", {
          p_provider: providerId,
          p_key: form.apiKey.trim(),
        });
        if (keyError) {
          setError(`Provider saved, but the key was not stored: ${keyError.message}`);
          return;
        }
      }

      setForm(null);
      router.refresh();
    } finally {
      setSaving(false);
    }
  }

  async function handleClearKey(p: LlmProvider) {
    setError(null);
    setBusyId(p.id);
    const supabase = createClient();
    try {
      const { error: clearError } = await supabase.rpc("clear_llm_provider_key", {
        p_provider: p.id,
      });
      if (clearError) {
        setError(clearError.message);
        return;
      }
      router.refresh();
    } finally {
      setBusyId(null);
    }
  }

  async function handleMakeDefault() {
    if (!defaultPick) return;
    setError(null);
    setBusyId(defaultPick.provider.id);
    const supabase = createClient();
    try {
      // Clear the old default first — at most one per org (partial unique index).
      const current = providers.find((p) => p.is_default);
      if (current && current.id !== defaultPick.provider.id) {
        const { error: clearError } = await supabase
          .from("llm_providers")
          .update({ is_default: false })
          .eq("id", current.id)
          .eq("org_id", orgId);
        if (clearError) {
          setError(clearError.message);
          return;
        }
      }
      const { error: setError_ } = await supabase
        .from("llm_providers")
        .update({ is_default: true, default_model: defaultPick.model })
        .eq("id", defaultPick.provider.id)
        .eq("org_id", orgId);
      if (setError_) {
        setError(setError_.message);
        return;
      }
      setDefaultPick(null);
      router.refresh();
    } finally {
      setBusyId(null);
    }
  }

  async function handleDelete(p: LlmProvider) {
    const note =
      providers.length === 1
        ? "Delete the last provider? The org goes back to unconfigured — thinking features will prompt for a provider again. Its stored key and function mappings are removed."
        : "Delete this provider? Its stored key is removed and any functions mapped to it fall back to the default.";
    if (
      !(await confirmDialog({
        title: "Delete provider?",
        description: note,
        confirmLabel: "Delete",
        destructive: true,
      }))
    )
      return;
    setError(null);
    setBusyId(p.id);
    const supabase = createClient();
    try {
      const { error: deleteError } = await supabase
        .from("llm_providers")
        .delete()
        .eq("id", p.id)
        .eq("org_id", orgId);
      if (deleteError) {
        setError(deleteError.message);
        return;
      }
      router.refresh();
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="grid gap-4">
      {providers.length === 0 ? (
        <EmptyState
          icon={BrainCircuit}
          title="No LLM providers yet"
          description="Add a provider to power the app's thinking jobs — the first one becomes the default."
        />
      ) : (
        <ul className="grid gap-2">
          {providers.map((p) => (
            <li
              key={p.id}
              className="flex items-center justify-between gap-3 rounded-md border px-3 py-2 text-sm"
            >
              <span className="flex min-w-0 items-center gap-3">
                <BrainCircuit className="size-4 shrink-0 text-muted-foreground" />
                <span className="flex min-w-0 flex-col">
                  <span className="flex items-center gap-2">
                    <span className="truncate font-medium">{p.name}</span>
                    {p.is_default && (
                      <Badge variant="secondary" title="Unmapped functions and failed calls land here">
                        <Star className="size-3" />
                        Default{p.default_model ? ` · ${p.default_model}` : ""}
                      </Badge>
                    )}
                  </span>
                  <span className="truncate text-xs text-muted-foreground">
                    {providerTypeLabel(p.provider_type)}
                    {` · ${p.models.length} model${p.models.length === 1 ? "" : "s"}: ${p.models.join(", ")}`}
                    {p.key_last4 ? ` · Key set · ····${p.key_last4}` : " · No key"}
                  </span>
                  {keyLooksWrong(p.key_last4) && (
                    <span className="text-xs font-medium text-destructive">
                      The stored key ends in &quot;{p.key_last4}&quot; — that
                      looks like an email tail, not an API key. Replace it.
                    </span>
                  )}
                </span>
              </span>
              <span className="flex shrink-0 items-center gap-2">
                {!p.is_default && (
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={busyId === p.id}
                    onClick={() =>
                      setDefaultPick({ provider: p, model: p.models[0] ?? "" })
                    }
                    title="Make this the org default (fallback for unmapped functions and failed calls)"
                  >
                    <Star className="size-4" />
                    Make default
                  </Button>
                )}
                <Button
                  variant="outline"
                  size="sm"
                  disabled={busyId === p.id}
                  onClick={() => openEdit(p)}
                >
                  <Pencil className="size-4" />
                  Edit
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={
                    busyId === p.id || (p.is_default && providers.length > 1)
                  }
                  title={
                    p.is_default && providers.length > 1
                      ? "Pick another default before deleting this provider"
                      : "Delete this provider"
                  }
                  onClick={() => handleDelete(p)}
                >
                  {busyId === p.id ? (
                    <Loader2 className="size-4 animate-spin" />
                  ) : (
                    <Trash2 className="size-4" />
                  )}
                </Button>
              </span>
            </li>
          ))}
        </ul>
      )}

      <Button className="justify-self-start" onClick={openCreate}>
        <Plus className="size-4" />
        Add provider
      </Button>

      <Dialog open={form !== null} onOpenChange={(open) => !open && setForm(null)}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>{form?.id ? "Edit provider" : "Add provider"}</DialogTitle>
            <DialogDescription>
              A named provider with its own key and a manually curated model
              list. Functions pick from these models on the routing table below.
            </DialogDescription>
          </DialogHeader>
          {form && (
            <form onSubmit={handleSave} className="grid gap-4">
              <div className="grid gap-2">
                <Label htmlFor="provider-name">Name</Label>
                <Input
                  id="provider-name"
                  placeholder="e.g. Anthropic production"
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                />
              </div>

              <div className="grid gap-2">
                <Label htmlFor="provider-type">Provider</Label>
                <Select
                  items={PROVIDER_TYPES}
                  value={form.providerType}
                  onValueChange={(v) => {
                    if (typeof v === "string")
                      setForm({ ...form, providerType: v });
                  }}
                >
                  <SelectTrigger
                    id="provider-type"
                    className="w-full"
                    disabled={!!form.id}
                  >
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {PROVIDER_TYPES.map((p) => (
                      <SelectItem key={p.value} value={p.value}>
                        {p.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                {form.id && (
                  <p className="text-xs text-muted-foreground">
                    The provider type is fixed — add a new provider to switch
                    vendors.
                  </p>
                )}
              </div>

              {isOllama && (
                <div className="grid gap-2">
                  <Label htmlFor="provider-base-url">Base URL</Label>
                  <Input
                    id="provider-base-url"
                    type="url"
                    placeholder="http://localhost:11434"
                    value={form.baseUrl}
                    onChange={(e) => setForm({ ...form, baseUrl: e.target.value })}
                  />
                </div>
              )}

              <div className="grid gap-2">
                <Label htmlFor="provider-model">Models</Label>
                {form.models.length > 0 && (
                  <div className="flex flex-wrap gap-1.5">
                    {form.models.map((m) => (
                      <Badge key={m} variant="secondary" className="gap-1 font-mono">
                        {m}
                        <button
                          type="button"
                          aria-label={`Remove ${m}`}
                          className="hover:text-destructive"
                          onClick={() => removeModel(m)}
                        >
                          <X className="size-3" />
                        </button>
                      </Badge>
                    ))}
                  </div>
                )}
                <div className="flex gap-2">
                  <Input
                    id="provider-model"
                    list="provider-model-suggestions"
                    placeholder={suggestions[0] ?? "model id"}
                    value={newModel}
                    onChange={(e) => setNewModel(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") {
                        e.preventDefault();
                        addModel();
                      }
                    }}
                  />
                  <datalist id="provider-model-suggestions">
                    {suggestions.map((m) => (
                      <option key={m} value={m} />
                    ))}
                  </datalist>
                  <Button type="button" variant="outline" onClick={addModel}>
                    <Plus className="size-4" />
                    Add
                  </Button>
                </div>
                <p className="text-xs text-muted-foreground">
                  Model ids are entered manually — type any id the provider
                  supports and press Add.
                </p>
              </div>

              <div className="grid gap-2">
                <Label htmlFor="provider-api-key">API key</Label>
                {editing?.key_last4 && (
                  <div className="flex items-center justify-between rounded-md border bg-muted/40 px-3 py-2 text-sm">
                    <span className="flex items-center gap-2">
                      <KeyRound className="size-4 text-muted-foreground" />
                      Key set · ends in ····{editing.key_last4}
                    </span>
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      onClick={() => handleClearKey(editing)}
                      disabled={busyId === editing.id}
                    >
                      {busyId === editing.id ? (
                        <Loader2 className="size-4 animate-spin" />
                      ) : (
                        <Trash2 className="size-4" />
                      )}
                      Remove
                    </Button>
                  </div>
                )}
                <Input
                  id="provider-api-key"
                  type="password"
                  // US-31.4: Chrome ignores autoComplete="off" on password
                  // fields; "new-password" is the value it honors. This is
                  // how an email ended up stored as the org's Anthropic key.
                  autoComplete="new-password"
                  placeholder={
                    editing?.key_last4
                      ? "Enter a new key to replace the stored one"
                      : "Paste your provider API key"
                  }
                  value={form.apiKey}
                  onChange={(e) => setForm({ ...form, apiKey: e.target.value })}
                />
                <p className="text-xs text-muted-foreground">
                  Stored write-only in Supabase Vault — it can be replaced or
                  removed, never read back.
                </p>
              </div>

              {error && (
                <p className="text-sm font-medium text-destructive">{error}</p>
              )}

              <Button type="submit" disabled={saving}>
                {saving && <Loader2 className="size-4 animate-spin" />}
                {form.id ? "Save changes" : "Add provider"}
              </Button>
            </form>
          )}
        </DialogContent>
      </Dialog>

      <Dialog
        open={defaultPick !== null}
        onOpenChange={(open) => !open && setDefaultPick(null)}
      >
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Make “{defaultPick?.provider.name}” the default</DialogTitle>
            <DialogDescription>
              Unmapped functions run here, and a failed routed call is retried
              once on this target. Pick the model the fallback uses.
            </DialogDescription>
          </DialogHeader>
          {defaultPick && (
            <div className="grid gap-4">
              <div className="grid gap-2">
                <Label htmlFor="default-model">Default model</Label>
                <Select
                  items={defaultPick.provider.models.map((m) => ({
                    value: m,
                    label: m,
                  }))}
                  value={defaultPick.model}
                  onValueChange={(v) => {
                    if (typeof v === "string")
                      setDefaultPick({ ...defaultPick, model: v });
                  }}
                >
                  <SelectTrigger id="default-model" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {defaultPick.provider.models.map((m) => (
                      <SelectItem key={m} value={m}>
                        {m}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <Button
                onClick={handleMakeDefault}
                disabled={busyId === defaultPick.provider.id || !defaultPick.model}
              >
                {busyId === defaultPick.provider.id && (
                  <Loader2 className="size-4 animate-spin" />
                )}
                Set as default
              </Button>
            </div>
          )}
        </DialogContent>
      </Dialog>

      {error && form === null && (
        <p className="text-sm font-medium text-destructive">{error}</p>
      )}
    </div>
  );
}
