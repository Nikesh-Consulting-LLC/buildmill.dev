import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
  ]),
  {
    // eslint-plugin-react-hooks 7 (via eslint-config-next 16) promoted its
    // React-Compiler rule set to errors, retroactively failing ~26 sites of
    // the codebase's established load-on-mount/reset-on-navigation idiom.
    // Refactoring those is per-file behavioral work, not a lint chore, so
    // these four stay visible as warnings; fix files to the strict idiom as
    // they get touched. Decided 2026-07-21.
    rules: {
      "react-hooks/set-state-in-effect": "warn",
      "react-hooks/purity": "warn",
      "react-hooks/static-components": "warn",
      "react-hooks/refs": "warn",
    },
  },
]);

export default eslintConfig;
