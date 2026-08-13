"use client";

import { useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import { Loader2, Plus } from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import {
  CUSTOM_SECTION_KEY,
  GUIDELINE_CATALOG,
  type CatalogSectionKey,
} from "@/lib/project-guidelines-catalog";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export function AddGuidelineSection({
  orgId,
  projectId,
  existingKeys,
  nextSortOrder,
}: {
  orgId: string;
  projectId: string;
  existingKeys: string[];
  nextSortOrder: number;
}) {
  const router = useRouter();
  const [customOpen, setCustomOpen] = useState(false);
  const [customTitle, setCustomTitle] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const available = GUIDELINE_CATALOG.filter((s) => !existingKeys.includes(s.key));

  async function addSection(sectionKey: CatalogSectionKey | "custom", title: string) {
    setError(null);
    setSaving(true);
    const supabase = createClient();
    // US-5.17: catalog picks start from the effective factory default
    // (superadmin override, else baked skeleton) — copied into the
    // project's own row now, never re-synced afterward. Custom sections
    // have no default and start empty.
    let content = "";
    if (sectionKey !== CUSTOM_SECTION_KEY) {
      const { data } = await supabase.rpc("effective_guideline_section", {
        p_key: sectionKey,
      });
      content = data ?? "";
    }
    const { error: dbError } = await supabase.from("project_guidelines").insert({
      org_id: orgId,
      project_id: projectId,
      section_key: sectionKey,
      title,
      content,
      sort_order: nextSortOrder,
    });
    setSaving(false);
    if (dbError) {
      setError(dbError.message);
      return;
    }
    setCustomOpen(false);
    setCustomTitle("");
    router.refresh();
  }

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger render={<Button variant="outline" size="sm" />}>
          <Plus className="size-4" />
          Add section
        </DropdownMenuTrigger>
        <DropdownMenuContent className="w-72">
          {available.some((s) => s.essential) && (
            <>
              <DropdownMenuGroup>
                <DropdownMenuLabel>Essentials</DropdownMenuLabel>
                {available
                  .filter((s) => s.essential)
                  .map((s) => (
                    <DropdownMenuItem
                      key={s.key}
                      onClick={() => addSection(s.key, s.title)}
                    >
                      <div className="flex flex-col gap-0.5 py-0.5">
                        <span>{s.title}</span>
                        <span className="text-xs text-muted-foreground">
                          {s.guidance}
                        </span>
                      </div>
                    </DropdownMenuItem>
                  ))}
              </DropdownMenuGroup>
              <DropdownMenuSeparator />
            </>
          )}
          {available
            .filter((s) => !s.essential)
            .map((s) => (
              <DropdownMenuItem key={s.key} onClick={() => addSection(s.key, s.title)}>
                <div className="flex flex-col gap-0.5 py-0.5">
                  <span>{s.title}</span>
                  <span className="text-xs text-muted-foreground">{s.guidance}</span>
                </div>
              </DropdownMenuItem>
            ))}
          <DropdownMenuSeparator />
          <DropdownMenuItem onClick={() => setCustomOpen(true)}>
            Custom section…
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      <Dialog open={customOpen} onOpenChange={setCustomOpen}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>Custom section</DialogTitle>
            <DialogDescription>Give this section a title.</DialogDescription>
          </DialogHeader>
          <div className="grid gap-2">
            <Label htmlFor="custom-section-title">Title</Label>
            <Input
              id="custom-section-title"
              value={customTitle}
              onChange={(e) => setCustomTitle(e.target.value)}
              placeholder="Deployment notes"
            />
          </div>
          {error && (
            <p className="text-sm font-medium text-destructive">{error}</p>
          )}
          <DialogFooter>
            <Button
              disabled={saving || !customTitle.trim()}
              onClick={() => addSection(CUSTOM_SECTION_KEY, customTitle.trim())}
            >
              {saving && <Loader2 className="size-4 animate-spin" />}
              Add section
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
