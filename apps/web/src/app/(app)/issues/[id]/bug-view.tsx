import { OriginReportSection } from "./origin-report-section";
import { WorkItemTabs } from "./work-item-tabs";
import { tabsForType, type WorkItemTab } from "./work-item-tab-config";
import {
  AcceptanceCriteriaSection,
  BugReportSection,
  ComplexitySection,
  ParentPrdSection,
  PlanSection,
  commonSlots,
  type WorkItemViewData,
} from "./work-item-sections";

/** US-15.20: a bug leads with the report — what happened and what should
 * have. Everything else follows the story shape. */
export function BugView({
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
      tabs={tabsForType("bug", { hasRelease })}
      type="bug"
      defaultTab={defaultTab}
      slots={{
        overview: (
          <div className="flex flex-col gap-6">
            <ComplexitySection data={data} />
            <ParentPrdSection data={data} />
            {/* US-16.7: renders nothing unless this bug was promoted from an
                app report — then it says so, and links back. */}
            <OriginReportSection issueId={data.issue.id} />
            <BugReportSection data={data} />
            <AcceptanceCriteriaSection data={data} />
          </div>
        ),
        plan: <PlanSection data={data} />,
        ...commonSlots(data),
      }}
    />
  );
}
