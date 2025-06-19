module.exports = {
  env: {
    browser: true, // For client-side JS in static/js
    es2021: true,
    jquery: true    // Assuming jQuery might be used based on typical web apps
  },
  extends: 'eslint:recommended',
  parserOptions: {
    ecmaVersion: 12,
    sourceType: 'module'
  },
  rules: {
    'no-unused-vars': 'warn', // Warn about unused variables instead of erroring
    'no-console': 'off',      // Allow console.log statements (useful for debugging)
    // Add any other project-specific rules or overrides here
  }
};
