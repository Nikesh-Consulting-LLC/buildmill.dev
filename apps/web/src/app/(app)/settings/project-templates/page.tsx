// Phase 67 (us-67.3): Owner/Admin decide which superadmin project templates
// are copied into the org and fine-tune the copies; every other role reads
// the same lists, with every mutating control disabled — enforced at the
// database by RLS (org_project_templates/org_project_template_sections
// write policies require manage_project), not just hidden here.
//
// us-100.4: a template is the files a project will publish — the document
// (`agent_instructions`) plus the `worker_instruction` sections. The count
// shown per template is filled files, so only those rows are counted here.

import { requireOrg } from "../require-org";
import { loadOrgCapabilities } from "@/lib/permissions";
import { ProjectTemplatesClient, type OrgTemplate } from "./project-templates-client";

export default async function ProjectTemplatesPage() {
  const { supabase, user, orgId } = await requireOrg();
  const caps = await loadOrgCapabilities(supabase, orgId, user.id);

  // US-118.1/118.2: the face — image_path, updated_at (cache-bust), category.
  const { data: globalTemplates } = await supabase
    .from("project_templates")
    .select("id, key, name, description, category, is_default, image_path, updated_at")
    .order("sort_order", { ascending: true });

  const { data: orgTemplates } = await supabase
    .from("org_project_templates")
    .select(
      "id, template_key, name, description, category, image_path, updated_at, is_default, is_available, archived_at, agent_instructions",
    )
    .eq("org_id", orgId)
    .order("sort_order", { ascending: true });

  const orgTemplateIds = (orgTemplates ?? []).map((t) => t.id);
  const { data: filledSections } = orgTemplateIds.length
    ? await supabase
        .from("org_project_template_sections")
        .select("org_template_id")
        .in("org_template_id", orgTemplateIds)
        .eq("section_type", "worker_instruction")
        .neq("content", "")
    : { data: [] };

  const fileCounts: Record<string, number> = {};
  for (const s of filledSections ?? []) {
    fileCounts[s.org_template_id] = (fileCounts[s.org_template_id] ?? 0) + 1;
  }

  return (
    <ProjectTemplatesClient
      orgId={orgId}
      canManage={caps.can("manage_project")}
      globalTemplates={globalTemplates ?? []}
      orgTemplates={(orgTemplates ?? []) as OrgTemplate[]}
      fileCounts={fileCounts}
    />
  );
}
