/** US-5.17/US-5.18: shared shapes for the admin template library pages. */

export type TemplateOverride = {
  content: string;
  updated_at: string;
  updated_by: string | null;
};

export type TemplateGroup = "thinking" | "worker" | "guideline" | "help";

export type TemplateItem = {
  key: string;
  group: TemplateGroup;
  label: string;
  description: string;
  variables: string[];
  default: string;
  override: TemplateOverride | null;
};

export const GROUP_ORDER: TemplateGroup[] = [
  "thinking",
  "worker",
  "guideline",
  "help",
];

export const GROUP_META: Record<TemplateGroup, { badge: string }> = {
  thinking: { badge: "Thinking prompt" },
  worker: { badge: "Worker default" },
  guideline: { badge: "Guideline section" },
  help: { badge: "Help content" },
};
