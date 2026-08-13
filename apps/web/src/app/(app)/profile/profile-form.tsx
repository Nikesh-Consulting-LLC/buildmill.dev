"use client";

import { useRef, useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
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

    setPreview((prev) => {
      if (prev?.startsWith("blob:")) URL.revokeObjectURL(prev);
      return URL.createObjectURL(file);
    });
    setPendingFile(file);
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
      <div className="flex items-center gap-2">
        <Label>Email</Label>
        <span className="text-sm text-muted-foreground">{email}</span>
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
