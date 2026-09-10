import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      globals: globals.browser,
    },
    rules: {
      // `[SecureContext]` APIs are absent on `http://0.0.0.0:8000` and over LAN,
      // where Crucible is routinely opened — a bare use is a TypeError there, not
      // a graceful degradation. See PM-024.
      'no-restricted-syntax': [
        'error',
        {
          selector: "MemberExpression[object.name='navigator'][property.name='clipboard']",
          message: 'navigator.clipboard is [SecureContext] and absent on http://0.0.0.0 and LAN origins. Use copyText() from utils/clipboard.ts (PM-024).',
        },
        {
          selector: "MemberExpression[object.name='crypto'][property.name='randomUUID']",
          message: 'crypto.randomUUID is [SecureContext] and absent on http://0.0.0.0 and LAN origins. Use nanoid() from store/nanoid.ts (PM-024).',
        },
      ],
    },
  },
  // The clipboard helper is the one place allowed to touch the API it wraps.
  {
    files: ['src/utils/clipboard.ts'],
    rules: { 'no-restricted-syntax': 'off' },
  },
  // Playwright specs and config run in Node, not the browser — without node
  // globals here `npm run lint` fails on `process`, `console`, etc.
  {
    files: ['e2e/**/*.ts', 'playwright.config.ts'],
    languageOptions: {
      globals: globals.node,
    },
    // The specs run browser code inside `page.evaluate`, where naming these APIs
    // is the point — `insecure-origin.spec.ts` asserts one of them is *gone*.
    rules: { 'no-restricted-syntax': 'off' },
  },
])
