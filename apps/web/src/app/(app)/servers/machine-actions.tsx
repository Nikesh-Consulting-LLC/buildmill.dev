"use client";

import { useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import {
  FolderOpen,
  Loader2,
  Plug,
  ShieldAlert,
  Terminal,
  Trash2,
} from "lucide-react";
import { apiCall, ApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { ServerDialog } from "./server-dialog";
import type { ServerRow } from "./server-card";

/** Open a machine tool in a minimal, chrome-less popup window (no address
 * bar / toolbar / menus, where the browser allows it). */
function openServerPopup(href: string, name: string) {
  window.open(
    href,
    name,
    "popup=yes,width=1024,height=720,location=no,menubar=no,toolbar=no,status=no,scrollbars=yes,resizable=yes"
  );
}

/**
 * US-35.2: the actions on a machine — SSH, files, connectivity test, edit,
 * delete. Extracted from `ServerCard` so the list card and the machine detail
 * page drive the same code: a "Test" that behaves differently depending on
 * which surface you clicked it from is exactly the drift this phase is about.
 */
export function MachineActions({
  orgId,
  server,
  onDeleted,
}: {
  orgId: string;
  server: ServerRow;
  /** Where to go after a delete. The list just refreshes; the detail page has
   *  to leave, because the thing it was showing is gone. */
  onDeleted?: () => void;
}) {
  const router = useRouter();
  const [test, setTest] = useState<
    | { kind: "idle" }
    | { kind: "testing" }
    | { kind: "ok" }
    | { kind: "error"; message: string }
    | { kind: "host_key_changed"; message: string }
  >({ kind: "idle" });
  const [retrusting, setRetrusting] = useState(false);

  async function handleTest() {
    setTest({ kind: "testing" });
    try {
      await apiCall(`/api/v1/servers/${server.id}/test`, { method: "POST" });
      setTest({ kind: "ok" });
      router.refresh();
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        setTest({ kind: "host_key_changed", message: e.message });
      } else {
        setTest({ kind: "error", message: (e as Error).message });
      }
    }
  }

  async function handleRetrust() {
    setRetrusting(true);
    try {
      await apiCall(`/api/v1/servers/${server.id}/trust-host-key`, {
        method: "POST",
      });
      setTest({ kind: "idle" });
      router.refresh();
    } catch (e) {
      setTest({ kind: "error", message: (e as Error).message });
    } finally {
      setRetrusting(false);
    }
  }

  async function handleDelete() {
    await apiCall(`/api/v1/servers/${server.id}`, { method: "DELETE" });
    if (onDeleted) onDeleted();
    else router.refresh();
  }

  return (
    <div className="flex flex-col gap-2">
      {test.kind === "ok" && (
        <p className="text-xs font-medium text-emerald-600 dark:text-emerald-400">
          Connection succeeded.
        </p>
      )}
      {test.kind === "error" && (
        <p className="text-xs font-medium text-destructive">{test.message}</p>
      )}
      {test.kind === "host_key_changed" && (
        <div className="flex flex-col gap-2 rounded-md border border-destructive/40 bg-destructive/5 p-2">
          <span className="flex items-center gap-1.5 text-xs font-medium text-destructive">
            <ShieldAlert className="size-3.5" />
            {test.message}
          </span>
          <Button
            variant="destructive"
            size="sm"
            className="w-fit"
            disabled={retrusting}
            onClick={handleRetrust}
          >
            {retrusting && <Loader2 className="size-4 animate-spin" />}
            Trust new host key
          </Button>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <Button
          size="sm"
          variant="outline"
          onClick={() => openServerPopup(`/terminal/${server.id}`, `ssh_${server.id}`)}
        >
          <Terminal className="size-4" />
          SSH
        </Button>
        <Button
          size="sm"
          variant="outline"
          onClick={() => openServerPopup(`/files/${server.id}`, `files_${server.id}`)}
        >
          <FolderOpen className="size-4" />
          Files
        </Button>
        <Button
          size="sm"
          variant="outline"
          onClick={handleTest}
          disabled={test.kind === "testing"}
        >
          {test.kind === "testing" ? (
            <Loader2 className="size-4 animate-spin" />
          ) : (
            <Plug className="size-4" />
          )}
          Test
        </Button>
        <ServerDialog orgId={orgId} server={server} />
        <ConfirmDialog
          trigger={
            <Button size="sm" variant="outline">
              <Trash2 className="size-4" />
            </Button>
          }
          title={`Delete ${server.name}?`}
          description="This removes the machine and its stored credentials. This can't be undone."
          confirmLabel="Delete machine"
          onConfirm={handleDelete}
        />
      </div>
    </div>
  );
}
