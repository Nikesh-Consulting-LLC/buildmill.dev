import { notFound, redirect } from "next/navigation";
import { createClient } from "@/lib/supabase/server";
import { TerminalView } from "@/app/(app)/servers/[id]/terminal/terminal-view";

// Standalone, chrome-less SSH terminal for the "Pop out" window. Lives
// outside the (app) route group so it renders without the sidebar/top bar —
// just a title strip and the terminal filling the window.
export default async function TerminalPopoutPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ agentSlot?: string }>;
}) {
  const { id } = await params;
  const { agentSlot } = await searchParams;
  const supabase = await createClient();

  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/login");

  const { data: server } = await supabase
    .from("servers")
    .select("id, name, host, port, username")
    .eq("id", id)
    .maybeSingle();
  if (!server) notFound();

  // US-55.6: the Claude Terminal path identifies the session by agent, not
  // by machine — the title bar shouldn't leak the underlying server's host/
  // username to whoever's looking over the tab, so it's swapped for the
  // agent's own name and its org when this came from that action.
  let heading = { primary: server.name, secondary: `${server.username}@${server.host}:${server.port}` };
  if (agentSlot) {
    const { data: slot } = await supabase
      .from("agent_slots")
      .select("principals(display_name), organizations(name)")
      .eq("id", agentSlot)
      .maybeSingle();
    type Embedded<T> = T | T[] | null;
    const unwrap = <T,>(v: Embedded<T>): T | null => (Array.isArray(v) ? (v[0] ?? null) : v);
    const principal = unwrap(slot?.principals as Embedded<{ display_name: string | null }>);
    const org = unwrap(slot?.organizations as Embedded<{ name: string }>);
    if (principal?.display_name) {
      heading = { primary: principal.display_name, secondary: org?.name ?? "" };
    }
  }

  // Fixed full-window layout (not an svh calc): a pop-up window's viewport
  // height is unstable while it's still opening, and xterm can't fit against
  // a 0/oversized container. `fixed inset-0` always spans the live window, so
  // the terminal has a stable, correct height from first paint.
  return (
    <div className="fixed inset-0 flex flex-col gap-2 bg-background p-3">
      <div className="flex items-center gap-2">
        <span className="text-sm font-semibold tracking-tight">{heading.primary}</span>
        <span className="truncate font-mono text-xs text-muted-foreground">
          {heading.secondary}
        </span>
      </div>
      <TerminalView serverId={server.id} agentSlotId={agentSlot} />
    </div>
  );
}
