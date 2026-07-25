import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{js,jsx}'],
    extends: [
      js.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
      parserOptions: {
        ecmaVersion: 'latest',
        ecmaFeatures: { jsx: true },
        sourceType: 'module',
      },
    },
    rules: {
      'no-unused-vars': ['error', { varsIgnorePattern: '^[A-Z_]' }],
      // Existing dashboard hooks coordinate loaded data and theme state in effects.
      'react-hooks/set-state-in-effect': 'off',
      // Context modules intentionally export both providers and consumer hooks.
      'react-refresh/only-export-components': 'off',
    },
  },
  {
    files: ['**/*.test.js', 'scripts/*.mjs'],
    languageOptions: {
      globals: globals.node,
    },
  },
])
