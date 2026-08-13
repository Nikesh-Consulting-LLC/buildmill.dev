// US-14.11: which environment is this build talking to?
// NEXT_PUBLIC_ENV_LABEL is the explicit switch; when unset the label is
// derived from the Supabase project ref, so a misconfigured machine can
// read as Dev but never silently as Prod. Inlined at build time.

const PROD_REF = "wdudmfhhqxrqzoyhuzwx";

/** Environment label to display, or null on prod (no indicator). */
export function envLabel(): string | null {
  const explicit = process.env.NEXT_PUBLIC_ENV_LABEL?.trim();
  if (explicit) {
    return explicit.toLowerCase() === "prod" ? null : explicit.toUpperCase();
  }
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL ?? "";
  return url.includes(`${PROD_REF}.supabase.co`) ? null : "DEV";
}

/** Amber recolor for logo images outside prod; empty string on prod. */
export function envLogoTint(): string {
  return envLabel() ? "[filter:sepia(1)_saturate(4)_hue-rotate(-12deg)]" : "";
}
