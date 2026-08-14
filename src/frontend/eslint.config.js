import js from '@eslint/js'
import globals from 'globals'
import tseslint from 'typescript-eslint'
import pluginVue from 'eslint-plugin-vue'
import vueParser from 'vue-eslint-parser'

export default tseslint.config(
  { ignores: ['dist'] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  ...pluginVue.configs['flat/recommended'],
  {
    files: ['**/*.vue'],
    languageOptions: {
      parser: vueParser,
      parserOptions: {
        parser: tseslint.parser,
        extraFileExtensions: ['.vue'],
      },
    },
    rules: {
      'vue/multi-word-component-names': 'off',
    },
  },
  {
    // Tests intentionally colocate small inline Vue stubs. Production prop
    // contracts are enforced in maintained components, not throwaway stubs.
    files: ['**/*.{test,spec}.ts'],
    rules: {
      'vue/one-component-per-file': 'off',
      'vue/require-default-prop': 'off',
      'vue/require-prop-types': 'off',
    },
  },
  {
    languageOptions: {
      globals: {
        ...globals.browser,
        // Vite `define` compile-time flag (see vite.config.ts / vite-env.d.ts).
        __HFL_EXTENSIONS_FRONTEND__: 'readonly',
      },
    },
    rules: {
      '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_' }],
    },
  },
)
