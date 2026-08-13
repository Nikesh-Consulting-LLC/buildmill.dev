import { WorkItemTabs } from "./work-item-tabs";
import { tabsForType, type WorkItemTab } from "./work-item-tab-config";
import {
  ComplexitySection,
  DescriptionSection,
  PrdSection,
  StoriesSection,
  commonSlots,
  type WorkItemViewData,
} from "./work-item-sections";

/** US-15.20: a feature is its requirement and the stories it breaks into.
 * Its PRD is a first-class tab, and it has no Plan tab — a feature never
 * carries a plan; its stories do.
 *
 * US-49.4: the stories are a tab of their own after the PRD, rather than a
 * list under the prose on the first tab. They are the surface a manager opens
 * a feature to use, and a requirement longer than a paragraph pushed them
 * below the fold. */
export function FeatureView({
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
      tabs={tabsForType("feature", { hasRelease })}
      type="feature"
      defaultTab={defaultTab}
      slots={{
        overview: (
          <div className="flex flex-col gap-6">
            <ComplexitySection data={data} />
            {/* US-49.3: not "Description" — this prose is the brief a PRD run
                is dispatched against, and the heading is what tells a manager
                to write one rather than a summary. */}
            <DescriptionSection
              data={data}
              title="Feature requirement"
              empty="No requirement written yet."
            />
          </div>
        ),
        prd: <PrdSection data={data} />,
        stories: <StoriesSection data={data} />,
        ...commonSlots(data),
      }}
    />
  );
}
