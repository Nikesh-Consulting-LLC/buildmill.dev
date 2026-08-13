import { WorkItemTabs } from "./work-item-tabs";
import { tabsForType, type WorkItemTab } from "./work-item-tab-config";
import {
  ComplexitySection,
  DescriptionSection,
  ParentPrdSection,
  PlanSection,
  commonSlots,
  type WorkItemViewData,
} from "./work-item-sections";

/** US-15.20: a chore is the plainest of the four — a description and a plan.
 * Chores carry no acceptance criteria today, so Overview shows none rather
 * than an empty card. */
export function ChoreView({
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
      tabs={tabsForType("chore", { hasRelease })}
      type="chore"
      defaultTab={defaultTab}
      slots={{
        overview: (
          <div className="flex flex-col gap-6">
            <ComplexitySection data={data} />
            <ParentPrdSection data={data} />
            <DescriptionSection data={data} title="Description" />
          </div>
        ),
        plan: <PlanSection data={data} />,
        ...commonSlots(data),
      }}
    />
  );
}
