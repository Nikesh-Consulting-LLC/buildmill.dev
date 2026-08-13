"use server";

import { cookies } from "next/headers";
import { revalidatePath } from "next/cache";
import { GLOBAL_PROJECTS_COOKIE } from "./global-project-selection";

/** `null` clears the cookie back to "all". An empty array is a real
 * selection (nothing checked), distinct from unset. */
export async function setGlobalProjects(ids: string[] | null) {
  const store = await cookies();
  if (ids === null) {
    store.delete(GLOBAL_PROJECTS_COOKIE);
  } else {
    store.set(GLOBAL_PROJECTS_COOKIE, ids.join(","), {
      path: "/",
      maxAge: 60 * 60 * 24 * 365,
      sameSite: "lax",
    });
  }
  // The (app) route group has no path segment of its own, so "/" covers
  // every page under it.
  revalidatePath("/", "layout");
}
