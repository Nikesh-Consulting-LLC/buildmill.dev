"use client";

import { useRouter } from "@/lib/router-with-progress";
import Link from "next/link";
import { LogOut, User } from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

export function UserMenu({
  email,
  displayName,
  avatarUrl,
  side = "bottom",
  align = "end",
  label,
}: {
  email: string;
  displayName?: string | null;
  avatarUrl?: string | null;
  side?: "top" | "bottom" | "left" | "right";
  align?: "start" | "center" | "end";
  /** When set, the trigger includes this text beside the avatar, so the whole
   * avatar + name area opens the menu (not just the avatar). */
  label?: string;
}) {
  const router = useRouter();

  async function signOut() {
    const supabase = createClient();
    await supabase.auth.signOut();
    router.push("/login");
    router.refresh();
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={
          label ? (
            <Button
              variant="ghost"
              className="h-8 min-w-0 shrink justify-start gap-2 rounded-full px-1"
            />
          ) : (
            <Button variant="ghost" size="icon" className="rounded-full" />
          )
        }
      >
        <Avatar className="size-8 shrink-0">
          <AvatarImage src={avatarUrl ?? undefined} alt={displayName ?? email} />
          <AvatarFallback className="text-xs uppercase">
            {(displayName ?? email).slice(0, 2)}
          </AvatarFallback>
        </Avatar>
        {label && (
          <span className="min-w-0 truncate text-xs font-medium text-muted-foreground">
            {label}
          </span>
        )}
      </DropdownMenuTrigger>
      <DropdownMenuContent side={side} align={align} className="w-56">
        <DropdownMenuGroup>
          <DropdownMenuLabel className="font-normal">
            <p className="text-xs text-muted-foreground">Signed in as</p>
            <p className="truncate text-sm font-medium">{displayName || email}</p>
          </DropdownMenuLabel>
        </DropdownMenuGroup>
        <DropdownMenuSeparator />
        <DropdownMenuItem render={<Link href="/profile" />}>
          <User className="size-4" />
          Profile
        </DropdownMenuItem>
        <DropdownMenuItem onClick={signOut}>
          <LogOut className="size-4" />
          Sign out
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
