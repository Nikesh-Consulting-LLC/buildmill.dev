import { WorkItemTabs } from "./work-item-tabs";
import { tabsForType, type WorkItemTab } from "./work-item-tab-config";
import {
  AcceptanceCriteriaSection,
  ComplexitySection,
  DescriptionSection,
  ParentPrdSection,
  PlanSection,
  commonSlots,
  type WorkItemViewData,
} from "./work-item-sections";

/** US-15.20: a story is the thing that actually gets planned and built —
 * its own text, its acceptance criteria, and the parent feature's approved
 * PRD for context. It has a Plan tab and no PRD of its own. */
export function StoryView({
  data,
  defaultTab,
  hasRelease,
}: {
  data: WorkItemViewData;
  defaultTab: WorkItemTab;
  hasRelease: boolean;
}) {
  return (
    <WorkItemTabs
      tabs={tabsForType("story", { hasRelease })}
      type="story"
      defaultTab={defaultTab}
      slots={{
        overview: (
          <div className="flex flex-col gap-6">
            <ComplexitySection data={data} />
            <ParentPrdSection data={data} />
            <DescriptionSection data={data} title="Story" />
            <AcceptanceCriteriaSection data={data} />
          </div>
        ),
        plan: <PlanSection data={data} />,
        ...commonSlots(data),
      }}
    />
  );
}
