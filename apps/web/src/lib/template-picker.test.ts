import { test } from "node:test";
import assert from "node:assert/strict";
import {
  FILTER_BOX_THRESHOLD,
  categoriesOf,
  defaultTemplateId,
  filterTemplates,
  showCategoryChips,
  showFilterBox,
} from "./template-picker.ts";

// US-118.3 AC11: the row's rules, pinned.

const t = (id: string, name: string, category = "", is_default = false, description = "") => ({
  id,
  name,
  category,
  is_default,
  description,
});

test("default: the marked default, else the first, else nothing", () => {
  assert.equal(defaultTemplateId([t("a", "A"), t("b", "B", "", true), t("c", "C")]), "b");
  assert.equal(defaultTemplateId([t("a", "A"), t("b", "B")]), "a");
  assert.equal(defaultTemplateId([]), "");
});

test("chips: none for zero or one category, shown at two; empty strings ignored", () => {
  assert.equal(showCategoryChips([t("a", "A"), t("b", "B")]), false);
  assert.equal(showCategoryChips([t("a", "A", "General"), t("b", "B", "General"), t("c", "C", "")]), false);
  assert.equal(showCategoryChips([t("a", "A", "General"), t("b", "B", "Service")]), true);
  assert.deepEqual(categoriesOf([t("a", "A", " General "), t("b", "B", "Service"), t("c", "C", "General")]), [
    "General",
    "Service",
  ]);
});

test("filter box: only past six templates", () => {
  const six = Array.from({ length: FILTER_BOX_THRESHOLD }, (_, i) => t(String(i), `T${i}`));
  assert.equal(showFilterBox(six), false);
  assert.equal(showFilterBox([...six, t("x", "Seventh")]), true);
});

test("filterTemplates: by category, by query on name and description, combined", () => {
  const all = [
    t("a", "Generic Web App", "General", false, "A **frontend and API** in one repo."),
    t("b", "FastAPI service", "Service", false, "A headless HTTP service."),
    t("c", "Static site", "Site", false, "Content pages, no backend."),
  ];
  assert.deepEqual(filterTemplates(all, "all", "").map((x) => x.id), ["a", "b", "c"]);
  assert.deepEqual(filterTemplates(all, "Service", "").map((x) => x.id), ["b"]);
  assert.deepEqual(filterTemplates(all, "all", "web").map((x) => x.id), ["a"]);
  assert.deepEqual(filterTemplates(all, "all", "BACKEND").map((x) => x.id), ["c"]);
  assert.deepEqual(filterTemplates(all, "Site", "backend").map((x) => x.id), ["c"]);
  assert.deepEqual(filterTemplates(all, "Site", "frontend").map((x) => x.id), []);
});

test("filtering is not selection: the chosen id survives being filtered out", () => {
  const all = [t("a", "A", "General", true), t("b", "B", "Service")];
  const chosen = defaultTemplateId(all);
  const shown = filterTemplates(all, "Service", "");
  assert.equal(chosen, "a");
  assert.equal(shown.some((x) => x.id === chosen), false);
  // The component states `chosen` under the row regardless — nothing here
  // ever rewrites it.
});
