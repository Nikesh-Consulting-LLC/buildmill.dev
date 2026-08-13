"use client";

import { useRef, useState } from "react";
import {
  Bold,
  Code,
  Heading,
  ImagePlus,
  Italic,
  Link as LinkIcon,
  List,
  ListOrdered,
  ListTodo,
  Quote,
  SquareCode,
  Strikethrough,
  Table,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { createClient } from "@/lib/supabase/client";
import {
  continueListOnEnter,
  insertCodeBlock,
  insertLink,
  insertTable,
  toggleLinePrefix,
  toggleNumberedList,
  wrapSelection,
  type EditResult,
} from "@/lib/markdown-edit";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { MarkdownView } from "@/components/markdown-view";

/** US-5.15: the shared GitHub-style markdown editor — a formatting toolbar,
 * Write/Preview tabs, and keyboard shortcuts over a plain textarea. Value
 * in/out stays a raw markdown string; Preview renders through MarkdownView,
 * the same code path as every read view. */

type ToolbarAction = {
  icon: typeof Bold;
  label: string;
  run: (value: string, start: number, end: number) => EditResult;
};

const ACTIONS: ToolbarAction[] = [
  {
    icon: Heading,
    label: "Heading",
    run: (v, s, e) => toggleLinePrefix(v, s, e, "### "),
  },
  { icon: Bold, label: "Bold (Ctrl+B)", run: (v, s, e) => wrapSelection(v, s, e, "**") },
  {
    icon: Italic,
    label: "Italic (Ctrl+I)",
    run: (v, s, e) => wrapSelection(v, s, e, "_"),
  },
  {
    icon: Strikethrough,
    label: "Strikethrough",
    run: (v, s, e) => wrapSelection(v, s, e, "~~"),
  },
  { icon: LinkIcon, label: "Link (Ctrl+K)", run: insertLink },
  {
    icon: Code,
    label: "Inline code",
    run: (v, s, e) => wrapSelection(v, s, e, "`", "`", "code"),
  },
  { icon: SquareCode, label: "Code block", run: insertCodeBlock },
  {
    icon: Quote,
    label: "Quote",
    run: (v, s, e) => toggleLinePrefix(v, s, e, "> "),
  },
  {
    icon: List,
    label: "Bullet list",
    run: (v, s, e) => toggleLinePrefix(v, s, e, "- "),
  },
  { icon: ListOrdered, label: "Numbered list", run: toggleNumberedList },
  {
    icon: ListTodo,
    label: "Task list",
    run: (v, s, e) => toggleLinePrefix(v, s, e, "- [ ] "),
  },
  { icon: Table, label: "Insert table", run: insertTable },
];

const IMAGE_TYPES: Record<string, string> = {
  "image/png": "png",
  "image/jpeg": "jpg",
  "image/gif": "gif",
  "image/webp": "webp",
};
const MAX_IMAGE_BYTES = 5 * 1024 * 1024;

export function MarkdownEditor({
  value,
  onChange,
  placeholder,
  rows = 6,
  disabled,
  id,
  className,
  orgId,
  defaultTab = "write",
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  rows?: number;
  disabled?: boolean;
  id?: string;
  className?: string;
  /** US-5.16: enables image paste/drop/upload — files land org-scoped in
   * the private `attachments` bucket and markdown gets an attachment://
   * ref. Omit on surfaces with no org context. */
  orgId?: string;
  /** US-5.18: which tab is active on mount — a "view" entry point opens
   * on Preview; the editor's own tabs switch freely afterwards. */
  defaultTab?: "write" | "preview";
}) {
  const [tab, setTab] = useState<"write" | "preview">(defaultTab);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  // async upload completion must edit the value as it is THEN, not as it
  // was when the upload started — mirror the latest value in a ref
  const valueRef = useRef(value);
  valueRef.current = value;

  async function uploadImage(file: File) {
    if (!orgId) return;
    setUploadError(null);
    const ext = IMAGE_TYPES[file.type];
    if (!ext) {
      setUploadError(`"${file.name}" is not a supported image (png/jpg/gif/webp).`);
      return;
    }
    if (file.size > MAX_IMAGE_BYTES) {
      setUploadError(`"${file.name}" is over the 5 MB image limit.`);
      return;
    }

    const el = textareaRef.current;
    const placeholderText = `![Uploading ${file.name}…]()`;
    const at = el ? el.selectionStart : valueRef.current.length;
    const withPlaceholder =
      valueRef.current.slice(0, at) +
      placeholderText +
      valueRef.current.slice(at);
    onChange(withPlaceholder);

    const path = `${orgId}/uploads/${crypto.randomUUID()}.${ext}`;
    const { error } = await createClient()
      .storage.from("attachments")
      .upload(path, file, { contentType: file.type });

    const replacement = error
      ? ""
      : `![${file.name}](attachment://${path})`;
    const current = valueRef.current;
    const next = current.includes(placeholderText)
      ? current.replace(placeholderText, replacement)
      : current + (replacement ? `\n${replacement}` : "");
    onChange(next);
    if (error) {
      setUploadError(`Upload of "${file.name}" failed: ${error.message}`);
    }
  }

  async function uploadAll(files: FileList | File[]) {
    for (const file of Array.from(files)) {
      await uploadImage(file);
    }
  }

  function apply(run: ToolbarAction["run"]) {
    const el = textareaRef.current;
    if (!el) return;
    const result = run(value, el.selectionStart, el.selectionEnd);
    onChange(result.value);
    requestAnimationFrame(() => {
      el.focus();
      el.setSelectionRange(result.selectionStart, result.selectionEnd);
    });
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if ((e.ctrlKey || e.metaKey) && !e.altKey) {
      const key = e.key.toLowerCase();
      if (key === "b") {
        e.preventDefault();
        apply((v, s, en) => wrapSelection(v, s, en, "**"));
        return;
      }
      if (key === "i") {
        e.preventDefault();
        apply((v, s, en) => wrapSelection(v, s, en, "_"));
        return;
      }
      if (key === "k") {
        e.preventDefault();
        apply(insertLink);
        return;
      }
    }
    if (e.key === "Enter" && !e.shiftKey) {
      const el = e.currentTarget;
      const result = continueListOnEnter(value, el.selectionStart);
      if (result) {
        e.preventDefault();
        onChange(result.value);
        requestAnimationFrame(() => {
          el.focus();
          el.setSelectionRange(result.selectionStart, result.selectionEnd);
        });
      }
    }
  }

  return (
    <div className={cn("rounded-lg border border-input", className)}>
      <div className="flex flex-wrap items-center gap-0.5 border-b bg-muted/40 px-1.5 py-1">
        <div className="mr-2 flex items-center gap-1">
          <ToolbarTab active={tab === "write"} onClick={() => setTab("write")}>
            Write
          </ToolbarTab>
          <ToolbarTab
            active={tab === "preview"}
            onClick={() => setTab("preview")}
          >
            Preview
          </ToolbarTab>
        </div>
        <div className="ml-auto flex flex-wrap items-center">
          {ACTIONS.map((a) => (
            <Button
              key={a.label}
              type="button"
              variant="ghost"
              size="icon-sm"
              aria-label={a.label}
              title={a.label}
              disabled={disabled || tab === "preview"}
              onClick={() => apply(a.run)}
            >
              <a.icon className="size-3.5" />
            </Button>
          ))}
          {orgId && (
            <>
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                aria-label="Attach an image"
                title="Attach an image"
                disabled={disabled || tab === "preview"}
                onClick={() => fileInputRef.current?.click()}
              >
                <ImagePlus className="size-3.5" />
              </Button>
              <input
                ref={fileInputRef}
                type="file"
                accept={Object.keys(IMAGE_TYPES).join(",")}
                multiple
                hidden
                onChange={(e) => {
                  if (e.target.files?.length) uploadAll(e.target.files);
                  e.target.value = "";
                }}
              />
            </>
          )}
        </div>
      </div>

      {tab === "write" ? (
        <Textarea
          ref={textareaRef}
          id={id}
          rows={rows}
          value={value}
          disabled={disabled}
          placeholder={placeholder}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={handleKeyDown}
          onPaste={(e) => {
            if (!orgId) return;
            const files = Array.from(e.clipboardData.files).filter((f) =>
              f.type.startsWith("image/")
            );
            if (files.length) {
              e.preventDefault();
              uploadAll(files);
            }
          }}
          onDragOver={(e) => {
            if (orgId && e.dataTransfer.types.includes("Files")) {
              e.preventDefault();
            }
          }}
          onDrop={(e) => {
            if (!orgId) return;
            const files = Array.from(e.dataTransfer.files).filter((f) =>
              f.type.startsWith("image/")
            );
            if (files.length) {
              e.preventDefault();
              uploadAll(files);
            }
          }}
          className="rounded-t-none border-0 focus-visible:ring-0 focus-visible:border-0 font-mono text-xs"
        />
      ) : (
        <div className="min-h-16 px-3 py-2">
          {value.trim() ? (
            <MarkdownView>{value}</MarkdownView>
          ) : (
            <p className="text-sm text-muted-foreground">
              Nothing to preview.
            </p>
          )}
        </div>
      )}
      {uploadError && (
        <p className="border-t px-3 py-1.5 text-xs font-medium text-destructive">
          {uploadError}
        </p>
      )}
    </div>
  );
}

function ToolbarTab({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "rounded-md px-2 py-1 text-xs font-medium transition-colors",
        active
          ? "bg-background text-foreground shadow-sm"
          : "text-muted-foreground hover:text-foreground"
      )}
    >
      {children}
    </button>
  );
}
