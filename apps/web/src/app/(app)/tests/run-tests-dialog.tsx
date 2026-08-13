"use client";

import { useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import { Loader2, Play } from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { CATALOG_TEST_TYPES, ENVIRONMENTS } from "./test-case-dialog";
import type { TestCaseRow } from "./test-library";

const ALL = "all";

export function RunTestsDialog({
  orgId,
  projectId,
  userId,
  testCases,
}: {
  orgId: string;
  projectId: string;
  userId: string;
  testCases: TestCaseRow[];
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [environment, setEnvironment] = useState("dev");
  const [type, setType] = useState(ALL);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const allTypes = Array.from(
    new Set([...CATALOG_TEST_TYPES, ...testCases.flatMap((c) => c.test_types)])
  );

  // Abandoned tests never enter new runs; environment must match.
  const selection = testCases.filter(
    (c) =>
      c.status === "active" &&
      c.environments.includes(environment) &&
      (type === ALL || c.test_types.includes(type))
  );

  async function handleStart() {
    setError(null);
    setStarting(true);
    const supabase = createClient();
    try {
      const label = type === ALL ? `all · ${environment}` : `${type} · ${environment}`;
      const { data: run, error: runError } = await supabase
        .from("test_runs")
        .insert({
          org_id: orgId,
          project_id: projectId,
          environment,
          label,
          started_by: userId,
        })
        .select("id")
        .single();
      if (runError) {
        setError(runError.message);
        return;
      }

      const { error: resultsError } = await supabase
        .from("test_run_results")
        .insert(
          selection.map((c) => ({
            org_id: orgId,
            test_run_id: run.id,
            test_case_id: c.id,
          }))
        );
      if (resultsError) {
        setError(resultsError.message);
        return;
      }

      setOpen(false);
      router.push(`/tests/runs/${run.id}`);
    } finally {
      setStarting(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger render={<Button variant="outline" />}>
        <Play className="size-4" />
        Run tests
      </DialogTrigger>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Run tests</DialogTitle>
          <DialogDescription>
            Pick the environment and which tests to run. Results save as you
            record them — you can leave and resume the run.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-4">
          <div className="grid gap-2">
            <Label htmlFor="run-env">Environment</Label>
            <Select
              items={ENVIRONMENTS.map((e) => ({ value: e, label: e }))}
              value={environment}
              onValueChange={(v) => {
                if (typeof v === "string") setEnvironment(v);
              }}
            >
              <SelectTrigger id="run-env" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {ENVIRONMENTS.map((e) => (
                  <SelectItem key={e} value={e}>
                    {e}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="grid gap-2">
            <Label htmlFor="run-type">Test type</Label>
            <Select
              items={[
                { value: ALL, label: "All types" },
                ...allTypes.map((t) => ({ value: t, label: t })),
              ]}
              value={type}
              onValueChange={(v) => {
                if (typeof v === "string") setType(v);
              }}
            >
              <SelectTrigger id="run-type" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ALL}>All types</SelectItem>
                {allTypes.map((t) => (
                  <SelectItem key={t} value={t}>
                    {t}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <p className="text-sm text-muted-foreground">
            {selection.length} active test{selection.length === 1 ? "" : "s"}{" "}
            match{selection.length === 1 ? "es" : ""} this selection.
          </p>
          {error && (
            <p className="text-sm font-medium text-destructive">{error}</p>
          )}
        </div>
        <DialogFooter>
          <Button onClick={handleStart} disabled={starting || !selection.length}>
            {starting && <Loader2 className="size-4 animate-spin" />}
            Start run
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
