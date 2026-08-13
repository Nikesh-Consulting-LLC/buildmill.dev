"use client";

import { useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import { Rocket } from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

const NONE = "none";

/** US-21.1: which deployment a release ships to.
 *
 * A project can have several UAT deployments; without a designation the agent
 * holding a release run would pick one arbitrarily. UAT is required to cut a
 * release at all — Production is only needed once something is ready to be
 * promoted (us-21.5). */
export function ReleaseTargetsCard({
  projectId,
  uatDeploymentId,
  prodDeploymentId,
  deployments,
}: {
  projectId: string;
  uatDeploymentId: string | null;
  prodDeploymentId: string | null;
  deployments: { id: string; name: string; environment: string | null }[];
}) {
  const router = useRouter();
  const [uat, setUat] = useState(uatDeploymentId ?? NONE);
  const [prod, setProd] = useState(prodDeploymentId ?? NONE);
  const [error, setError] = useState<string | null>(null);

  async function save(column: string, value: string, revert: () => void) {
    setError(null);
    const supabase = createClient();
    const { error: dbError } = await supabase
      .from("projects")
      .update({ [column]: value === NONE ? null : value })
      .eq("id", projectId);
    if (dbError) {
      setError(dbError.message);
      revert();
      return;
    }
    router.refresh();
  }

  // Only deployments classified for that environment are offerable — a
  // release must not be able to ship UAT to a production target by a
  // mis-click on a dropdown.
  const forEnv = (env: string) =>
    deployments.filter((d) => d.environment === env);

  const field = (
    id: string,
    label: string,
    env: string,
    value: string,
    setValue: (v: string) => void,
    column: string,
    help: string
  ) => {
    const options = forEnv(env);
    return (
      <div className="grid gap-2">
        <Label htmlFor={id}>{label}</Label>
        {options.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No deployment is classified as {env} yet — set an environment on a
            deployment below first.
          </p>
        ) : (
          <Select
            items={[
              { value: NONE, label: "Not set" },
              ...options.map((d) => ({ value: d.id, label: d.name })),
            ]}
            value={value}
            onValueChange={(v) => {
              if (typeof v !== "string") return;
              const prev = value;
              setValue(v);
              save(column, v, () => setValue(prev));
            }}
          >
            <SelectTrigger id={id} className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={NONE}>Not set</SelectItem>
              {options.map((d) => (
                <SelectItem key={d.id} value={d.id}>
                  {d.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
        <p className="text-xs text-muted-foreground">{help}</p>
      </div>
    );
  };

  return (
    <Card className="min-w-0">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <Rocket className="size-4 text-muted-foreground" />
          Release targets
        </CardTitle>
        <CardDescription>
          Where a release goes. Every release ships to UAT first; Production is
          reached only by promotion, after you sign off.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4 sm:grid-cols-2">
        {field(
          "release-uat",
          "UAT",
          "uat",
          uat,
          setUat,
          "release_uat_deployment_id",
          "Required — a release cannot be cut without it."
        )}
        {field(
          "release-prod",
          "Production",
          "production",
          prod,
          setProd,
          "release_prod_deployment_id",
          "Needed to promote a signed-off release."
        )}
        {error && (
          <p className="text-sm font-medium text-destructive sm:col-span-2">
            {error}
          </p>
        )}
      </CardContent>
    </Card>
  );
}
