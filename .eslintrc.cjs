module.exports = {
  root: true,
  env: { node: true, es2022: true },
  parser: "@typescript-eslint/parser",
  plugins: ["@typescript-eslint", "import"],
  extends: [
    "eslint:recommended",
    "plugin:@typescript-eslint/recommended",
    "plugin:import/recommended",
    "plugin:import/typescript",
    "prettier",
  ],
  parserOptions: { sourceType: "module", project: false },
  settings: {
    "import/resolver": { typescript: { project: ["./tsconfig.json"] } },
  },
  rules: {
    "import/order": [
      "warn",
      {
        groups: [["builtin", "external"], ["internal"], ["parent", "sibling", "index"]],
        alphabetize: { order: "asc", caseInsensitive: true },
        "newlines-between": "always",
      },
    ],
    "@typescript-eslint/no-unused-vars": [
      "warn",
      { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
    ],
  },
};