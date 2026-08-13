// apps/web/src/app/(app)/settings/workers-help.tsx
"use client";

// US-3.7 — worker connection help & onboarding. Inline help on the
// Workers page: the worker model, per-tool numbered guides (rendering
// the same snippet source as the setup blocks — no drift), hand-back
// paths, and troubleshooting for the failure modes the API actually
// produces. Content lives here in versioned source, changing in the
// same PR as the behavior it documents.

import { useState } from "react";
import { BookOpen, Check, ChevronDown, Copy } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  BRANCH_CONVENTION,
  LEASES,
  pickSnippet,
  type Snippet,
} from "./worker-connect";

function SnippetBlock({ s }: { s: Snippet }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="flex items-start gap-2">
      <pre className="min-w-0 flex-1 overflow-x-auto rounded-md border bg-muted px-3 py-2 font-mono text-xs">
        {s.text}
      </pre>
      <Button
        variant="outline"
        size="sm"
        onClick={async () => {
          await navigator.clipboard.writeText(s.text);
          setCopied(true);
        }}
      >
        {copied ? (
          <Check className="size-4 text-green-600" />
        ) : (
          <Copy className="size-4" />
        )}
      </Button>
    </div>
  );
}

function Steps({ items }: { items: React.ReactNode[] }) {
  return (
    <ol className="grid list-decimal gap-2 pl-5 text-sm">
      {items.map((it, i) => (
        <li key={i}>{it}</li>
      ))}
    </ol>
  );
}

// US-14.7: was "the one-time reveal — it is never shown again". The token
// is stored in the vault and re-readable by its own principal (or an org
// admin) through Team → Connect; the roster's last-4 is a summary, not the
// only copy the app keeps.
const MINT_STEP = (
  <>
    Create the worker above (<em>Register worker</em>) and copy its token. You
    can read it again later from <em>Team → Connect</em> — only as its own
    principal, or as an org admin. It is a live credential: <em>Regenerate</em>{" "}
    if it ever leaks.
  </>
);

const VERIFY_STEP = (
  <>
    Verify the connection: after the tool&apos;s first authenticated call, the
    worker&apos;s row above shows <em>Last seen just now</em>. That is the
    success signal for every guide on this page.
  </>
);

// us-5.27: two hand-back transports, one review pipeline. Agents default
// to MCP-only; the git remote serves git-native workers.
const gitStep = (snippets: Snippet[]) => (
  <>
    Hand code back over one of two transports.{" "}
    <strong>MCP only (agents, no git tooling):</strong>{" "}
    <code className="font-mono text-xs">get_workspace</code> downloads the
    working tree as a zip pinned to a base commit; work on it locally, then{" "}
    <code className="font-mono text-xs">submit_changeset</code> hands the
    changed files back — the factory builds the commit, pushes the branch,
    and opens the PR itself.{" "}
    <strong>Git-native (humans in IDEs, large repos):</strong> clone the{" "}
    <strong>factory git remote</strong> from the work item&apos;s context (or
    below), authenticating with the same token as the HTTP Basic password —
    no GitHub account or credentials, and no PR to open: push{" "}
    <code className="rounded bg-muted px-1 font-mono text-xs">
      {BRANCH_CONVENTION}
    </code>{" "}
    and submit.
    <div className="mt-2">
      <SnippetBlock s={pickSnippet(snippets, "git")} />
    </div>
  </>
);

export function WorkersHelp({ snippets }: { snippets: Snippet[] }) {
  const [open, setOpen] = useState(false);
  const GIT_STEP = gitStep(snippets);

  return (
    <div className="rounded-md border">
      <button
        type="button"
        className="flex w-full items-center justify-between gap-2 px-4 py-3 text-sm font-medium"
        onClick={() => setOpen((v) => !v)}
      >
        <span className="flex items-center gap-2">
          <BookOpen className="size-4 text-muted-foreground" />
          How workers connect — setup guides &amp; troubleshooting
        </span>
        <ChevronDown
          className={`size-4 text-muted-foreground transition-transform ${
            open ? "rotate-180" : ""
          }`}
        />
      </button>

      {open && (
        <div className="grid gap-6 border-t px-4 py-4">
          <section className="grid gap-2">
            <h3 className="text-sm font-semibold">The worker model</h3>
            <p className="text-sm text-muted-foreground">
              A <strong>worker</strong> is anything that can claim factory work
              and hand it back — the autonomous runner, or you driving Claude
              Code / Cursor / OpenCode. Every worker follows the same loop:{" "}
              <strong>
                pool → claim → context → work → submit → review
              </strong>
              . Claiming locks the item with a lease ({LEASES.autonomous} for
              autonomous workers, {LEASES.human} for humans; any authenticated
              call on the run extends it). Code work lands on the branch{" "}
              <code className="rounded bg-muted px-1 font-mono text-xs">
                {BRANCH_CONVENTION}
              </code>{" "}
              over one of two transports: <strong>MCP only</strong> — an
              agent downloads the workspace and submits a changeset, and the
              factory does all the git server-side (the fit for sandboxed
              agents with no git tooling) — or the{" "}
              <strong>factory git remote</strong> for git-native workers
              (humans in IDEs, or repos above the MCP snapshot ceiling).
              Submitted plans land in the plan-review gate; submitted code
              lands in the review panel, where the factory has already opened
              the PR for you.
            </p>
            <p className="text-sm text-muted-foreground">
              If a lease expires: a run <em>with</em> pushed work is submitted
              automatically (nothing is lost); a run <em>without</em> pushes
              returns to the pool for anyone to claim.
            </p>
          </section>

          <section className="grid gap-3">
            <h3 className="text-sm font-semibold">Connect your tool</h3>
            <Tabs defaultValue="claude">
              <TabsList>
                <TabsTrigger value="claude">Claude Code</TabsTrigger>
                <TabsTrigger value="cursor">Cursor</TabsTrigger>
                <TabsTrigger value="opencode">OpenCode</TabsTrigger>
                <TabsTrigger value="runner">Runner</TabsTrigger>
              </TabsList>
              <TabsContent value="claude" className="pt-3">
                <Steps
                  items={[
                    MINT_STEP,
                    <>
                      Add the factory as an MCP server (paste your token in
                      place of the placeholder):
                      <div className="mt-2">
                        <SnippetBlock s={pickSnippet(snippets, "claude")} />
                      </div>
                    </>,
                    <>
                      In a Claude Code session, ask{" "}
                      <em>&quot;what factory work is available?&quot;</em> —
                      the agent lists the pool, claims an item, and pulls its
                      full context.
                    </>,
                    GIT_STEP,
                    VERIFY_STEP,
                  ]}
                />
              </TabsContent>
              <TabsContent value="cursor" className="pt-3">
                <Steps
                  items={[
                    MINT_STEP,
                    <>
                      Create <code className="font-mono text-xs">.cursor/mcp.json</code>{" "}
                      in your workspace:
                      <div className="mt-2">
                        <SnippetBlock s={pickSnippet(snippets, "cursor")} />
                      </div>
                    </>,
                    <>
                      Open Cursor&apos;s agent and ask for available factory
                      work; claim from there.
                    </>,
                    GIT_STEP,
                    VERIFY_STEP,
                  ]}
                />
              </TabsContent>
              <TabsContent value="opencode" className="pt-3">
                <Steps
                  items={[
                    MINT_STEP,
                    <>
                      Add the factory to{" "}
                      <code className="font-mono text-xs">opencode.json</code>:
                      <div className="mt-2">
                        <SnippetBlock s={pickSnippet(snippets, "opencode")} />
                      </div>
                    </>,
                    GIT_STEP,
                    VERIFY_STEP,
                  ]}
                />
              </TabsContent>
              <TabsContent value="runner" className="pt-3">
                <Steps
                  items={[
                    <>
                      Add an <em>agent</em> on the Team page and copy its worker
                      token (the Connect selector above fills it in for you).
                    </>,
                    <>
                      On any machine with Python 3.12+, set the environment and
                      start the supervisor runner:
                      <div className="mt-2">
                        <SnippetBlock s={pickSnippet(snippets, "runner")} />
                      </div>
                    </>,
                    <>
                      Which agent modules and model it uses are configured{" "}
                      <strong>server-side</strong> on the agent&apos;s runner
                      console — no model keys on the machine. It holds a
                      persistent socket, claims work, drives the module,
                      self-repairs, and submits; failures always report back.
                    </>,
                    <>
                      Verify: the agent shows <strong>online</strong> on its
                      runner console (Team → the agent → <em>Open runner console</em>).
                    </>,
                  ]}
                />
              </TabsContent>
            </Tabs>
          </section>

          <section className="grid gap-2">
            <h3 className="text-sm font-semibold">Getting work home</h3>
            <p className="text-sm text-muted-foreground">
              All idempotent: <strong>explicit submit</strong> (the MCP{" "}
              <code className="font-mono text-xs">submit_plan</code> /{" "}
              <code className="font-mono text-xs">submit_changeset</code> /{" "}
              <code className="font-mono text-xs">submit_code_work</code>{" "}
              tools) — or, on the git path, just{" "}
              <strong>push and walk away</strong>: when your lease expires
              with pushed commits, the factory auto-submits the branch, opens
              the PR, and moves the item to review. Pushing WIP while the
              claim is alive is safe — a push is not &quot;done&quot;.
              Expiring with <em>no</em> pushes returns the item to the pool.
            </p>
          </section>

          <section className="grid gap-2">
            <h3 className="text-sm font-semibold">Troubleshooting</h3>
            <ul className="grid gap-2 text-sm text-muted-foreground">
              <li>
                <strong className="text-foreground">401 invalid or revoked token</strong>{" "}
                — the token was revoked or mistyped. Regenerate on the worker&apos;s
                row and update your tool&apos;s config.
              </li>
              <li>
                <strong className="text-foreground">&quot;someone else took it&quot;</strong>{" "}
                — you lost a claim race. List the pool again and claim a
                different item.
              </li>
              <li>
                <strong className="text-foreground">Lease expired mid-work</strong>{" "}
                — if you pushed, the factory already submitted it (check the
                work item). If not, the item is back in the pool: claim it
                again and continue; your local branch is still valid.
              </li>
              <li>
                <strong className="text-foreground">&quot;another worker holds this run&quot;</strong>{" "}
                — it was re-claimed after your lease expired. Coordinate in
                review, or pick other work.
              </li>
              <li>
                <strong className="text-foreground">Push rejected by the factory remote</strong>{" "}
                — only{" "}
                <code className="rounded bg-muted px-1 font-mono text-xs">
                  {BRANCH_CONVENTION}
                </code>{" "}
                branches for runs <em>you</em> hold are writable; deletions and
                history rewrites are refused. Claim the item first, then push
                exactly the branch named in its context.
              </li>
            </ul>
          </section>
        </div>
      )}
    </div>
  );
}
