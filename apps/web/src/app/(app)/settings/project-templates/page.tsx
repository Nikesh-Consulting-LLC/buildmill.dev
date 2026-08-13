// Phase 67 (us-67.3): Owner/Admin decide which superadmin project templates
// are copied into the org and fine-tune the copies; every other role reads
// the same lists, with every mutating control disabled — enforced at the
// database by RLS (org_project_templates/org_project_template_sections
// write policies require manage_project), not just hidden here.

import { requireOrg } from "../require-org";
import { loadOrgCapabilities } from "@/lib/permissions";
import { ProjectTemplatesClient } from "./project-templates-client";

export default async function ProjectTemplatesPage() {
  const { supabase, user, orgId } = await requireOrg();
  const caps = await loadOrgCapabilities(supabase, orgId, user.id);

  const { data: globalTemplates } = await supabase
    .from("project_templates")
    .select("id, key, name, description, category, is_default")
    .order("sort_order", { ascending: true });

  const { data: orgTemplates } = await supabase
    .from("org_project_templates")
    .select(
      "id, template_key, name, description, is_default, is_available, archived_at",
    )
    .eq("org_id", orgId)
    .order("sort_order", { ascending: true });

  const orgTemplateIds = (orgTemplates ?? []).map((t) => t.id);
  const { data: sectionCounts } = orgTemplateIds.length
    ? await supabase
        .from("org_project_template_sections")
        .select("org_template_id")
        .in("org_template_id", orgTemplateIds)
    : { data: [] };

  return (
    <ProjectTemplatesClient
      orgId={orgId}
      canManage={caps.can("manage_project")}
      globalTemplates={globalTemplates ?? []}
      orgTemplates={orgTemplates ?? []}
      sectionCounts={sectionCounts ?? []}
    />
  );
}
