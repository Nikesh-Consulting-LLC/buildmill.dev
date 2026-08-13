import { redirect } from "next/navigation";

// US-9.14: the worker/token registry folded into the Team surface (each
// principal's tokens live in its detail).
export default function WorkersSettingsRedirect() {
  redirect("/team");
}
