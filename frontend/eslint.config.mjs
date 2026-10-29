import { createRequire } from "node:module";

import { defineConfig, globalIgnores } from "eslint/config";

// typescript-eslint needs the TypeScript 6 compiler API and refuses to load next to
// TypeScript 7 (https://github.com/typescript-eslint/typescript-eslint/issues/10940).
// eslint-config-next imports it unconditionally, so hand it an empty stand-in and lint
// TS files with the Babel parser that eslint-config-next already configures.
// Remove this once typescript-eslint supports TypeScript 7.
const require = createRequire(import.meta.url);
const nextEntry = require.resolve("eslint-config-next");
const tsEslintPath = createRequire(nextEntry).resolve("typescript-eslint");
require.cache[tsEslintPath] = {
  id: tsEslintPath,
  filename: tsEslintPath,
  loaded: true,
  exports: { plugin: {}, parser: {}, configs: { recommended: [] } },
};

const { default: nextVitals } = await import("eslint-config-next/core-web-vitals");

const eslintConfig = defineConfig([
  ...nextVitals.filter((config) => config.name !== "next/typescript"),
  globalIgnores([
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
  ]),
]);

export default eslintConfig;
