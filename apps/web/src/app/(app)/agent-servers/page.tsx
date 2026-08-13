import { redirect } from "next/navigation";

// US-35.2: agent servers were never a second kind of object — every row in
// `agent_servers` points at a row in `servers`. One Machines list now carries
// both, badged by where each machine sits in its lifecycle.
export default function AgentServersRedirect() {
  redirect("/servers");
}
