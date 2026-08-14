import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { requireOrg } from "../require-org";
import { ProvidersSection, type LlmProvider } from "./providers-section";
import { RatesSection } from "./rates-section";
import { RoutingSection, type LlmRoute } from "./routing-section";
import {
  SubscriptionSection,
  type ClaudeSubscription,
} from "./subscription-section";

export default async function LlmProvidersPage() {
  const { supabase, orgId } = await requireOrg();

  const { data: providers } = await supabase
    .from("llm_providers")
    .select(
      "id, name, provider_type, base_url, models, is_default, default_model, key_last4"
    )
    .eq("org_id", orgId)
    .order("created_at", { ascending: true });

  const { data: routes } = await supabase
    .from("llm_function_routes")
    .select("function_key, provider_id, model")
    .eq("org_id", orgId);

  // US-52.2: the factory-held Claude subscription token's fingerprint row —
  // the secret itself is in Vault and unreadable from here by design.
  const { data: subscription } = await supabase
    .from("claude_subscriptions")
    .select("key_last4, set_at, expires_at")
    .eq("org_id", orgId)
    .maybeSingle();

  const providerRows = (providers as LlmProvider[] | null) ?? [];

  return (
    <div className="grid gap-6">
      <Card>
        <CardHeader>
          <CardTitle>LLM providers</CardTitle>
          <CardDescription>
            Named providers the app&apos;s thinking jobs can use — each with its
            own write-only key and a manually curated model list. One is the
            default: unmapped functions run on it, and failed routed calls are
            retried there once. Coding agents on workers keep their own
            credentials.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <ProvidersSection orgId={orgId} providers={providerRows} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Claude subscription</CardTitle>
          <CardDescription>
            A Claude Code subscription token for agents whose billing is set to
            Claude Code — OAuth. Runs on it bypass the metered gateway and bill
            the connected Claude account instead; they appear off-meter in
            spend. Stored write-only, like provider keys.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <SubscriptionSection
            orgId={orgId}
            subscription={(subscription as ClaudeSubscription | null) ?? null}
          />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Function routing</CardTitle>
          <CardDescription>
            Which provider and model each thinking function uses. The function
            list is owned by the backend — new functions appear here
            automatically and run on the default until mapped.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <RoutingSection
            orgId={orgId}
            providers={providerRows}
            routes={(routes as LlmRoute[] | null) ?? []}
          />
        </CardContent>
      </Card>

      {/* us-95.1: the rates joined this page from the old Settings → Spend —
          they are configuration (the provider's published pricing), and this
          is the page about providers. The report they feed is /costs. */}
      <Card>
        <CardHeader>
          <CardTitle>Model rates</CardTitle>
          <CardDescription>
            What each model costs per million tokens. The gateway meters every
            call in tokens; these rates turn tokens into the dollars the Costs
            section reports.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <RatesSection orgId={orgId} />
        </CardContent>
      </Card>
    </div>
  );
}
