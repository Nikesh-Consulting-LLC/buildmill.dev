import { notFound, redirect } from "next/navigation";
import { createClient } from "@/lib/supabase/server";
import { FileManager } from "@/app/(app)/servers/[id]/files/file-manager";

// Standalone, chrome-less file manager for the "Pop out" window. Lives
// outside the (app) route group so it renders without the sidebar/top bar —
// just a title strip and the file manager filling the window.
export default async function FilesPopoutPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const supabase = await createClient();

  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/login");

  // us-94.1: pop-outs live outside the (app) layout, so they carry the beta
  // gate themselves — a pending account gets the gate, not a bare 404.
  const { data: profile } = await supabase
    .from("profiles")
    .select("approved_at")
    .eq("id", user.id)
    .maybeSingle();
  if (!profile?.approved_at) redirect("/gate");

  const { data: server } = await supabase
    .from("servers")
    .select("id, name, host, port, username")
    .eq("id", id)
    .maybeSingle();
  if (!server) notFound();

  return (
    <div className="flex w-full flex-col gap-3 p-3">
      <div className="flex items-center gap-2">
        <span className="text-sm font-semibold tracking-tight">{server.name}</span>
        <span className="truncate font-mono text-xs text-muted-foreground">
          {server.username}@{server.host}:{server.port}
        </span>
      </div>
      <FileManager serverId={server.id} />
    </div>
  );
}
