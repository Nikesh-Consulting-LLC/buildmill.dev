import { redirect } from "next/navigation";

// US-2.24: settings is submenu pages now; land on the first one.
export default function SettingsPage() {
  redirect("/settings/llm-providers");
}
