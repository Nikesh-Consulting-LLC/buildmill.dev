import { createClient } from "@/lib/supabase/client";

/** US-2.21/2.22: browser-side document CRUD — straight to Supabase under
 * RLS ("build less API"). Objects live in the private `project-docs`
 * bucket at `<org_id>/projects/<project_id>/<document_id>/<filename>`;
 * metadata in public.documents. Factory/agent writes go through `api`. */

export const DOCS_BUCKET = "project-docs";
export const MAX_DOCUMENT_BYTES = 25 * 1024 * 1024;

export type DocumentSource = "user" | "factory" | "agent";
export type DocumentAttachedTo = "project" | "work-item" | "prd" | "test-case";

export type DocumentRow = {
  id: string;
  org_id: string;
  project_id: string;
  issue_id: string | null;
  test_case_id: string | null;
  run_id: string | null;
  name: string;
  mime_type: string;
  size_bytes: number;
  storage_path: string;
  source: DocumentSource;
  attached_to: DocumentAttachedTo;
  created_by: string | null;
  created_at: string;
  updated_at: string;
};

/** Where an upload lands: exactly one link per document (US-2.22). */
export type DocumentTarget =
  | { attachedTo: "project" }
  | { attachedTo: "work-item" | "prd"; issueId: string }
  | { attachedTo: "test-case"; testCaseId: string };

/** Mirrors the api's safe_name: last path segment, storage-safe chars. */
export function safeName(name: string): string {
  const base = (name || "").replaceAll("\\", "/").split("/").pop()?.trim() ?? "";
  const cleaned = base.replace(/[^A-Za-z0-9._ ()-]/g, "_").replace(/^[. ]+|[. ]+$/g, "");
  return cleaned || "document";
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

export function isPreviewableImage(mime: string): boolean {
  return mime.startsWith("image/");
}

export function isPreviewableHtml(mime: string): boolean {
  return mime === "text/html";
}

function targetRefs(target: DocumentTarget): {
  issue_id: string | null;
  test_case_id: string | null;
} {
  return {
    issue_id: "issueId" in target ? target.issueId : null,
    test_case_id: "testCaseId" in target ? target.testCaseId : null,
  };
}

export function matchesTarget(doc: DocumentRow, target: DocumentTarget): boolean {
  if (doc.attached_to !== target.attachedTo) return false;
  const refs = targetRefs(target);
  return (
    doc.issue_id === refs.issue_id && doc.test_case_id === refs.test_case_id
  );
}

export async function uploadDocument(
  orgId: string,
  projectId: string,
  target: DocumentTarget,
  file: File
): Promise<DocumentRow> {
  if (file.size > MAX_DOCUMENT_BYTES) {
    throw new Error("Documents are limited to 25 MB.");
  }
  const supabase = createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();

  const id = crypto.randomUUID();
  const name = safeName(file.name);
  const path = `${orgId}/projects/${projectId}/${id}/${name}`;
  const mime = file.type || "application/octet-stream";

  const { error: uploadError } = await supabase.storage
    .from(DOCS_BUCKET)
    .upload(path, file, { contentType: mime });
  if (uploadError) throw new Error(uploadError.message);

  const { data, error } = await supabase
    .from("documents")
    .insert({
      id,
      org_id: orgId,
      project_id: projectId,
      ...targetRefs(target),
      attached_to: target.attachedTo,
      name,
      mime_type: mime,
      size_bytes: file.size,
      storage_path: path,
      source: "user",
      created_by: user?.id ?? null,
    })
    .select()
    .single();
  if (error) {
    // Don't strand the object behind a failed row insert.
    await supabase.storage.from(DOCS_BUCKET).remove([path]);
    throw new Error(error.message);
  }
  return data as DocumentRow;
}

/** US-2.22 "Replace file": new bytes, same document id and links. The
 * source badge flips to the last writer (here: the user). */
export async function replaceDocumentFile(
  doc: DocumentRow,
  file: File
): Promise<DocumentRow> {
  if (file.size > MAX_DOCUMENT_BYTES) {
    throw new Error("Documents are limited to 25 MB.");
  }
  const supabase = createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();

  const name = safeName(file.name);
  const mime = file.type || "application/octet-stream";
  const dir = doc.storage_path.slice(0, doc.storage_path.lastIndexOf("/"));
  const path = `${dir}/${name}`;

  const { error: uploadError } = await supabase.storage
    .from(DOCS_BUCKET)
    .upload(path, file, { contentType: mime, upsert: true });
  if (uploadError) throw new Error(uploadError.message);

  if (path !== doc.storage_path) {
    await supabase.storage.from(DOCS_BUCKET).remove([doc.storage_path]);
  }

  const { data, error } = await supabase
    .from("documents")
    .update({
      name,
      mime_type: mime,
      size_bytes: file.size,
      storage_path: path,
      source: "user",
      created_by: user?.id ?? null,
    })
    .eq("id", doc.id)
    .select()
    .single();
  if (error) throw new Error(error.message);
  return data as DocumentRow;
}

/** Delete removes the row and the object. */
export async function deleteDocument(doc: DocumentRow): Promise<void> {
  const supabase = createClient();
  const { error } = await supabase.from("documents").delete().eq("id", doc.id);
  if (error) throw new Error(error.message);
  await supabase.storage.from(DOCS_BUCKET).remove([doc.storage_path]);
}

export async function documentUrl(
  doc: DocumentRow,
  opts?: { download?: boolean }
): Promise<string> {
  const supabase = createClient();
  const { data, error } = await supabase.storage
    .from(DOCS_BUCKET)
    .createSignedUrl(doc.storage_path, 3600, {
      download: opts?.download ? doc.name : undefined,
    });
  if (error || !data) throw new Error(error?.message ?? "Could not sign URL");
  return data.signedUrl;
}
