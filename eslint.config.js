// eslint.config.js
import globals from "globals";

export default [
  {
    languageOptions: {
      ecmaVersion: 2021,
      sourceType: "module",
      globals: {
        ...globals.browser, // for client-side JS in static/js
        ...globals.jquery,  // Assuming jQuery might be used
        // ...globals.node, // Uncomment if you have Node.js scripts that need linting
      }
    },
    rules: {
      "no-unused-vars": "warn", // Warn about unused variables instead of erroring
      "no-console": "off",      // Allow console.log statements (useful for debugging)
      // Add any other project-specific rules or overrides here
    }
  }
];
