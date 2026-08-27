import js from "@eslint/js";
import vue from "eslint-plugin-vue";
import globals from "globals";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist/**", "coverage/**"] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  ...vue.configs["flat/essential"],
  {
    files: ["src/**/*.vue"],
    languageOptions: {
      parserOptions: { parser: tseslint.parser },
    },
  },
  {
    files: ["src/**/*.{ts,vue}", "tests/**/*.ts", "vite.config.ts"],
    languageOptions: {
      globals: { ...globals.browser, ...globals.node },
    },
    rules: {
      "vue/multi-word-component-names": "off",
    },
  },
);
