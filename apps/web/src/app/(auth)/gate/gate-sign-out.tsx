"use client";

import { useState } from "react";
import { Loader2 } from "lucide-react";
import { useRouter } from "@/lib/router-with-progress";
import { createClient } from "@/lib/supabase/client";
import { Button } from "@/components/ui/button";

export function GateSignOut() {
  const router = useRouter();
  const [loading, setLoading] = useState(false);

  async function signOut() {
    setLoading(true);
    const supabase = createClient();
    await supabase.auth.signOut();
    router.push("/login");
    router.refresh();
  }

  return (
    <Button
      variant="outline"
      className="w-full"
      disabled={loading}
      onClick={signOut}
    >
      {loading && <Loader2 className="size-4 animate-spin" />}
      Sign out
    </Button>
  );
}
