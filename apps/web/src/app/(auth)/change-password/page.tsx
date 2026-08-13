import { redirect } from "next/navigation";
import { createClient } from "@/lib/supabase/server";
import { ChangePasswordForm } from "./change-password-form";

// US-9.5: reached by the (app)-layout gate when must_change_password is set
// (admin-provisioned or after an admin reset). Lives outside the (app) group so
// the gate can redirect here without looping. Any signed-in user may also set a
// new password here; normal users are simply never redirected to it.
export default async function ChangePasswordPage() {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/login");

  return (
    <div className="flex min-h-0 flex-1 flex-col items-center justify-center-safe gap-6 overflow-y-auto bg-muted/40 p-4">
      <ChangePasswordForm userId={user.id} />
    </div>
  );
}
