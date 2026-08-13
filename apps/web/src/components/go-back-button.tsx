"use client";

import { useRouter } from "@/lib/router-with-progress";
import { ArrowLeft } from "lucide-react";
import { Button } from "@/components/ui/button";

export function GoBackButton() {
  const router = useRouter();
  return (
    <Button variant="outline" onClick={() => router.back()}>
      <ArrowLeft />
      Go back
    </Button>
  );
}
