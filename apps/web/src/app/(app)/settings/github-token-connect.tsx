"use client";

import { useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import { KeyRound, Loader2 } from "lucide-react";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";

// US-3.15: fine-grained PAT connect. The token is sent once to the api,
// validated against GitHub, and stored write-only in Vault — it is never
// readable again from anywhere in the UI.
export function GithubTokenConnect() {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [token, setToken] = useState("");
  const [repos, setRepos] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit() {
    setError(null);
    setSaving(true);
    try {
      await apiFetch("/api/v1/github/connections/pat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          token: token.trim(),
          repos: repos
            .split(/[\n,]/)
            .map((r) => r.trim())
            .filter(Boolean),
        }),
      });
      setOpen(false);
      setToken("");
      setRepos("");
      router.refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) {
          setToken("");
          setRepos("");
          setError(null);
        }
      }}
    >
      <DialogTrigger render={<Button variant="outline" />}>
        <KeyRound className="size-4" />
        Connect with token
      </DialogTrigger>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Connect GitHub with a token</DialogTitle>
          <DialogDescription>
            Paste a fine-grained personal access token and list the
            repositories it grants. The token is validated against GitHub,
            stored write-only, and never shown again. Pushes and merges made
            through it act as the token&apos;s GitHub user.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-4">
          <div className="grid gap-1.5">
            <Label htmlFor="gh-pat">Personal access token</Label>
            <Input
              id="gh-pat"
              type="password"
              autoComplete="new-password"
              placeholder="github_pat_…"
              value={token}
              onChange={(e) => setToken(e.target.value)}
            />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="gh-pat-repos">Repositories (one per line)</Label>
            <Textarea
              id="gh-pat-repos"
              placeholder={"owner/repo\nowner/other-repo"}
              rows={3}
              value={repos}
              onChange={(e) => setRepos(e.target.value)}
            />
            <p className="text-xs text-muted-foreground">
              A token&apos;s grants can&apos;t be listed automatically — enter
              the repos it should reach; each is checked live before saving.
            </p>
          </div>
          {error && (
            <p className="text-sm font-medium text-destructive">{error}</p>
          )}
        </div>
        <DialogFooter>
          <Button
            onClick={handleSubmit}
            disabled={saving || !token.trim() || !repos.trim()}
          >
            {saving && <Loader2 className="size-4 animate-spin" />}
            Validate &amp; connect
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
