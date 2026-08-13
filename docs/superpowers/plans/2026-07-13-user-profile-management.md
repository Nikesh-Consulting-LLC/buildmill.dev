# User Profile Management (us-1.24) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the operator a `/profile` page to edit display name, upload an avatar, and change their password.

**Architecture:** Plain Supabase-SDK CRUD from `apps/web`, no new FastAPI surface ("Build less API" — CLAUDE.md). One new migration adds `profiles.avatar_url` plus a new `avatars` Storage bucket with per-user write policies. `/profile` is a server component that loads the profile row and passes it to two client forms (`ProfileForm`, `ChangePasswordForm`). `user-menu.tsx` and `top-bar.tsx` gain a profile link and render the uploaded avatar.

**Tech Stack:** Next.js 16 App Router, Supabase JS SDK (`@supabase/ssr`), Supabase Storage, shadcn/Base UI components already in the repo.

## Global Constraints

- Migration files go in `infra/supabase/migrations/`, numbered — next free number is **`014`**. Apply it to the live Supabase project (MCP `apply_migration`) in the same change, then regenerate `apps/web/src/lib/supabase/database.types.ts` (MCP `generate_typescript_types`).
- `apps/web` has **no test runner configured** (confirmed: no vitest/jest, no test files) and the user has explicitly chosen to skip adding one for this story. Steps below replace the usual "write failing test → implement → pass" cycle with "implement → verify via `npm run build` + manual check in the browser preview → commit."
- This feature has no FastAPI endpoint, so it falls outside `apps/api`'s existing pytest harness (which only exercises FastAPI routes against a mocked JWT/DB — see `apps/api/tests/conftest.py`). The cross-user isolation the story asks about is enforced by the **existing** `profiles` RLS policy (`id = auth.uid()`), unchanged by this migration — verified by a direct SQL check during migration application (Task 1, Step 4), not a new pytest file. Flag this explicitly to the user in the final summary — don't silently claim pytest coverage that doesn't exist.
- Follow `apps/web/AGENTS.md`: this is Next.js 16 with breaking changes from training data — if anything about routing/storage APIs looks off, check `node_modules/next/dist/docs/` before guessing.
- shadcn here is **Base UI**, not Radix: triggers use `render={<Button />}`, not `asChild`.
- No comments in code unless explaining a genuinely non-obvious constraint (e.g. why avatar uploads reuse one fixed storage key).

---

### Task 1: Migration — `avatar_url` column + `avatars` Storage bucket

**Files:**
- Create: `infra/supabase/migrations/014_profile_avatar.sql`
- Modify (regenerate): `apps/web/src/lib/supabase/database.types.ts`

**Interfaces:**
- Produces: `public.profiles.avatar_url text` (nullable), Storage bucket id `avatars` (public read, max 2MB, image mime types only, writes scoped to `{auth.uid()}/...` path prefix).

- [ ] **Step 1: Write the migration**

```sql
-- 014_profile_avatar: adds avatar_url to profiles (US-1.24) and an
-- `avatars` Storage bucket. Object key convention is a fixed
-- `{user_id}/avatar` path (no extension) so re-uploading always
-- overwrites the same object — "replacing removes the previous image"
-- falls out of upsert instead of a separate list+delete step.

alter table public.profiles add column avatar_url text;

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
  'avatars',
  'avatars',
  true,
  2097152,
  array['image/png', 'image/jpeg', 'image/webp', 'image/gif']
)
on conflict (id) do nothing;

create policy "avatar images are publicly readable"
  on storage.objects for select
  using (bucket_id = 'avatars');

create policy "users can upload their own avatar"
  on storage.objects for insert
  with check (
    bucket_id = 'avatars'
    and (storage.foldername(name))[1] = (select auth.uid()::text)
  );

create policy "users can replace their own avatar"
  on storage.objects for update
  using (
    bucket_id = 'avatars'
    and (storage.foldername(name))[1] = (select auth.uid()::text)
  );

create policy "users can delete their own avatar"
  on storage.objects for delete
  using (
    bucket_id = 'avatars'
    and (storage.foldername(name))[1] = (select auth.uid()::text)
  );
```

- [ ] **Step 2: Apply the migration to the live Supabase project**

Use the Supabase MCP `apply_migration` tool with project id `wdudmfhhqxrqzoyhuzwx`, name `profile_avatar`, and the SQL above.

- [ ] **Step 3: Regenerate TypeScript types**

Use the Supabase MCP `generate_typescript_types` tool for project `wdudmfhhqxrqzoyhuzwx` and overwrite `apps/web/src/lib/supabase/database.types.ts` with the result.

- [ ] **Step 4: Verify RLS still isolates profiles per-user**

Use the Supabase MCP `execute_sql` tool to run:

```sql
select policyname, cmd, qual from pg_policies where tablename = 'profiles';
```

Expected: `users can update their own profile` and `users can view their own profile` still present with `qual` containing `auth.uid()`. This is the manual substitute for a pytest cross-user negative test noted in Global Constraints.

- [ ] **Step 5: Commit**

```bash
git add infra/supabase/migrations/014_profile_avatar.sql apps/web/src/lib/supabase/database.types.ts
git commit -m "feat: add profiles.avatar_url and avatars storage bucket"
```

---

### Task 2: `/profile` route — display name + avatar upload

**Files:**
- Create: `apps/web/src/app/(app)/profile/page.tsx`
- Create: `apps/web/src/app/(app)/profile/profile-form.tsx`
- Test/verify: manual browser check (no test runner)

**Interfaces:**
- Consumes: `profiles` row `{ id, email, display_name, avatar_url }` from `page.tsx` server load.
- Produces: `ProfileForm({ userId, email, displayName, avatarUrl }: { userId: string; email: string; displayName: string | null; avatarUrl: string | null })` — client component other tasks don't depend on directly.

- [ ] **Step 1: Server page**

```tsx
// apps/web/src/app/(app)/profile/page.tsx
import { redirect } from "next/navigation";
import { createClient } from "@/lib/supabase/server";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { ProfileForm } from "./profile-form";
import { ChangePasswordForm } from "./change-password-form";

export default async function ProfilePage() {
  const supabase = await createClient();

  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/login");

  const { data: profile } = await supabase
    .from("profiles")
    .select("id, email, display_name, avatar_url")
    .eq("id", user.id)
    .maybeSingle();

  return (
    <div className="mx-auto flex w-full max-w-2xl flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Profile</h1>
        <p className="text-sm text-muted-foreground">
          Your name, avatar, and password.
        </p>
      </div>
      <Card>
        <CardHeader>
          <CardTitle>Your profile</CardTitle>
          <CardDescription>
            Visible to the rest of your organization.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <ProfileForm
            userId={user.id}
            email={profile?.email ?? user.email ?? ""}
            displayName={profile?.display_name ?? null}
            avatarUrl={profile?.avatar_url ?? null}
          />
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Change password</CardTitle>
          <CardDescription>
            Requires your current password.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <ChangePasswordForm email={profile?.email ?? user.email ?? ""} />
        </CardContent>
      </Card>
    </div>
  );
}
```

- [ ] **Step 2: Profile form (display name + avatar upload)**

```tsx
// apps/web/src/app/(app)/profile/profile-form.tsx
"use client";

import { useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Loader2 } from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

const MAX_BYTES = 2 * 1024 * 1024;
const ALLOWED_TYPES = ["image/png", "image/jpeg", "image/webp", "image/gif"];

export function ProfileForm({
  userId,
  email,
  displayName,
  avatarUrl,
}: {
  userId: string;
  email: string;
  displayName: string | null;
  avatarUrl: string | null;
}) {
  const router = useRouter();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [name, setName] = useState(displayName ?? "");
  const [preview, setPreview] = useState<string | null>(avatarUrl);
  const [pendingFile, setPendingFile] = useState<File | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    setError(null);
    setSuccess(false);
    if (!file) return;

    if (!ALLOWED_TYPES.includes(file.type)) {
      setError("Avatar must be a PNG, JPEG, WEBP, or GIF image.");
      return;
    }
    if (file.size > MAX_BYTES) {
      setError("Avatar must be under 2MB.");
      return;
    }

    setPendingFile(file);
    setPreview(URL.createObjectURL(file));
  }

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSuccess(false);
    setSaving(true);

    const supabase = createClient();
    try {
      let nextAvatarUrl = avatarUrl;

      if (pendingFile) {
        const path = `${userId}/avatar`;
        const { error: uploadError } = await supabase.storage
          .from("avatars")
          .upload(path, pendingFile, { upsert: true, contentType: pendingFile.type });
        if (uploadError) {
          setError(uploadError.message);
          return;
        }
        const { data: publicUrl } = supabase.storage
          .from("avatars")
          .getPublicUrl(path);
        nextAvatarUrl = `${publicUrl.publicUrl}?t=${Date.now()}`;
      }

      const { error: updateError } = await supabase
        .from("profiles")
        .update({ display_name: name.trim() || null, avatar_url: nextAvatarUrl })
        .eq("id", userId);

      if (updateError) {
        setError(updateError.message);
        return;
      }

      setPendingFile(null);
      setSuccess(true);
      router.refresh();
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={handleSave} className="grid gap-4">
      <div className="flex items-center gap-4">
        <Avatar className="size-16">
          <AvatarImage src={preview ?? undefined} alt={name || email} />
          <AvatarFallback className="text-lg uppercase">
            {(name || email).slice(0, 2)}
          </AvatarFallback>
        </Avatar>
        <div className="grid gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => fileInputRef.current?.click()}
          >
            Change avatar
          </Button>
          <input
            ref={fileInputRef}
            type="file"
            accept={ALLOWED_TYPES.join(",")}
            className="hidden"
            onChange={handleFileChange}
          />
          <p className="text-xs text-muted-foreground">PNG, JPEG, WEBP, or GIF. Max 2MB.</p>
        </div>
      </div>
      <div className="grid gap-2">
        <Label htmlFor="display-name">Display name</Label>
        <Input
          id="display-name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Your name"
        />
      </div>
      <div className="grid gap-2">
        <Label>Email</Label>
        <Input value={email} disabled readOnly />
      </div>
      {error && <p className="text-sm font-medium text-destructive">{error}</p>}
      {success && (
        <p className="text-sm font-medium text-emerald-600 dark:text-emerald-400">
          Profile updated.
        </p>
      )}
      <div>
        <Button type="submit" disabled={saving}>
          {saving && <Loader2 className="size-4 animate-spin" />}
          Save changes
        </Button>
      </div>
    </form>
  );
}
```

- [ ] **Step 3: Build check**

Run: `npm run build` (from repo root or `apps/web` per existing convention) and confirm it completes with no type errors touching the new files.

- [ ] **Step 4: Commit**

```bash
git add apps/web/src/app/\(app\)/profile/page.tsx apps/web/src/app/\(app\)/profile/profile-form.tsx
git commit -m "feat: add /profile page with display name and avatar upload"
```

---

### Task 3: Change-password form

**Files:**
- Create: `apps/web/src/app/(app)/profile/change-password-form.tsx`

**Interfaces:**
- Consumes: `email` prop from `page.tsx` (Task 2).
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Implement**

```tsx
// apps/web/src/app/(app)/profile/change-password-form.tsx
"use client";

import { useState } from "react";
import { Loader2 } from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

const MIN_LENGTH = 8;

export function ChangePasswordForm({ email }: { email: string }) {
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSuccess(false);

    if (newPassword.length < MIN_LENGTH) {
      setError(`New password must be at least ${MIN_LENGTH} characters.`);
      return;
    }
    if (newPassword !== confirmPassword) {
      setError("New password and confirmation don't match.");
      return;
    }

    setSaving(true);
    const supabase = createClient();
    try {
      const { error: signInError } = await supabase.auth.signInWithPassword({
        email,
        password: currentPassword,
      });
      if (signInError) {
        setError("Current password is incorrect.");
        return;
      }

      const { error: updateError } = await supabase.auth.updateUser({
        password: newPassword,
      });
      if (updateError) {
        setError(updateError.message);
        return;
      }

      setCurrentPassword("");
      setNewPassword("");
      setConfirmPassword("");
      setSuccess(true);
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="grid gap-4">
      <div className="grid gap-2">
        <Label htmlFor="current-password">Current password</Label>
        <Input
          id="current-password"
          type="password"
          autoComplete="current-password"
          value={currentPassword}
          onChange={(e) => setCurrentPassword(e.target.value)}
          required
        />
      </div>
      <div className="grid gap-2">
        <Label htmlFor="new-password">New password</Label>
        <Input
          id="new-password"
          type="password"
          autoComplete="new-password"
          value={newPassword}
          onChange={(e) => setNewPassword(e.target.value)}
          required
          minLength={MIN_LENGTH}
        />
      </div>
      <div className="grid gap-2">
        <Label htmlFor="confirm-password">Confirm new password</Label>
        <Input
          id="confirm-password"
          type="password"
          autoComplete="new-password"
          value={confirmPassword}
          onChange={(e) => setConfirmPassword(e.target.value)}
          required
          minLength={MIN_LENGTH}
        />
      </div>
      {error && <p className="text-sm font-medium text-destructive">{error}</p>}
      {success && (
        <p className="text-sm font-medium text-emerald-600 dark:text-emerald-400">
          Password changed.
        </p>
      )}
      <div>
        <Button type="submit" disabled={saving}>
          {saving && <Loader2 className="size-4 animate-spin" />}
          Change password
        </Button>
      </div>
    </form>
  );
}
```

- [ ] **Step 2: Build check**

Run: `npm run build` and confirm no type errors.

- [ ] **Step 3: Commit**

```bash
git add apps/web/src/app/\(app\)/profile/change-password-form.tsx
git commit -m "feat: add change-password form to /profile"
```

---

### Task 4: Wire avatar + profile link into `user-menu.tsx` and `top-bar.tsx`

**Files:**
- Modify: `apps/web/src/components/user-menu.tsx`
- Modify: `apps/web/src/components/top-bar.tsx`
- Modify: `apps/web/src/app/(app)/layout.tsx`

**Interfaces:**
- Consumes: `profiles.display_name`, `profiles.avatar_url` (Task 1 columns).

- [ ] **Step 1: Update `user-menu.tsx`**

```tsx
// apps/web/src/components/user-menu.tsx
"use client";

import { useRouter } from "next/navigation";
import Link from "next/link";
import { LogOut, User } from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

export function UserMenu({
  email,
  displayName,
  avatarUrl,
}: {
  email: string;
  displayName?: string | null;
  avatarUrl?: string | null;
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
        render={<Button variant="ghost" size="icon" className="rounded-full" />}
      >
        <Avatar className="size-8">
          <AvatarImage src={avatarUrl ?? undefined} alt={displayName ?? email} />
          <AvatarFallback className="text-xs uppercase">
            {(displayName ?? email).slice(0, 2)}
          </AvatarFallback>
        </Avatar>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-56">
        <DropdownMenuLabel className="font-normal">
          <p className="text-xs text-muted-foreground">Signed in as</p>
          <p className="truncate text-sm font-medium">{displayName || email}</p>
        </DropdownMenuLabel>
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
```

- [ ] **Step 2: Update `top-bar.tsx`**

```tsx
// apps/web/src/components/top-bar.tsx
import { ThemeToggle } from "@/components/theme-toggle";
import { UserMenu } from "@/components/user-menu";

export function TopBar({
  orgName,
  email,
  displayName,
  avatarUrl,
}: {
  orgName: string;
  email: string;
  displayName?: string | null;
  avatarUrl?: string | null;
}) {
  return (
    <header className="flex h-14 items-center justify-between border-b bg-background px-4 md:px-6">
      <div className="flex items-center gap-2">
        <span className="text-sm font-medium text-muted-foreground md:hidden">
          Software Factory
        </span>
        <span className="hidden text-sm font-medium md:block">{orgName}</span>
      </div>
      <div className="flex items-center gap-1">
        <ThemeToggle />
        <UserMenu email={email} displayName={displayName} avatarUrl={avatarUrl} />
      </div>
    </header>
  );
}
```

- [ ] **Step 3: Update `layout.tsx` to fetch and pass profile fields**

Add alongside the existing `membership` query in `apps/web/src/app/(app)/layout.tsx`:

```tsx
const { data: profile } = await supabase
  .from("profiles")
  .select("display_name, avatar_url")
  .eq("id", user.id)
  .maybeSingle();
```

And update the `<TopBar />` call:

```tsx
<TopBar
  orgName={orgName}
  email={user.email ?? ""}
  displayName={profile?.display_name ?? null}
  avatarUrl={profile?.avatar_url ?? null}
/>
```

- [ ] **Step 4: Build check**

Run: `npm run build` and confirm it succeeds.

- [ ] **Step 5: Manual verification in browser preview**

Start the dev server, sign in, go to `/profile`, upload an avatar, change the display name, save, and confirm the sidebar/top-bar avatar updates. Then attempt a password change with a deliberately wrong current password and confirm the inline error appears.

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/components/user-menu.tsx apps/web/src/components/top-bar.tsx apps/web/src/app/\(app\)/layout.tsx
git commit -m "feat: show uploaded avatar and profile link in top bar"
```
