import js from '@eslint/js'
import query from '@tanstack/eslint-plugin-query'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import globals from 'globals'
import tseslint from 'typescript-eslint'

export default tseslint.config(
  {
    ignores: [
      'dist',
      'coverage',
      'node_modules',
      'src/api/schema.d.ts',
      'public/mockServiceWorker.js',
    ],
  },
  js.configs.recommended,
  ...tseslint.configs.recommendedTypeChecked,
  ...query.configs['flat/recommended'],
  reactHooks.configs.flat.recommended,
  {
    files: ['**/*.{ts,tsx}'],
    languageOptions: {
      globals: { ...globals.browser },
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
    plugins: { 'react-refresh': reactRefresh },
    rules: {
      'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],
      '@typescript-eslint/consistent-type-imports': 'error',
      '@typescript-eslint/no-floating-promises': 'error',
      // Правило интерфейса: логи, код и тексты от сервера выводятся только как текст.
      'no-restricted-syntax': [
        'error',
        {
          selector: "JSXAttribute[name.name='dangerouslySetInnerHTML']",
          message:
            'Запрещено: логи, код и серверные тексты выводятся только как текст (см. docs/team-guide.md, раздел 6).',
        },
      ],
      'no-restricted-properties': [
        'error',
        { property: 'innerHTML', message: 'Запрещено: выводите текст через React-узлы.' },
        { property: 'outerHTML', message: 'Запрещено: выводите текст через React-узлы.' },
        { property: 'insertAdjacentHTML', message: 'Запрещено: выводите текст через React-узлы.' },
      ],
    },
  },
  {
    // JS-файлы (сам конфиг ESLint) не входят в tsconfig: правила с типами для них отключаем.
    files: ['**/*.js'],
    ...tseslint.configs.disableTypeChecked,
  },
  {
    files: ['*.config.{js,ts}', 'vite.config.ts'],
    languageOptions: { globals: { ...globals.node } },
  },
  {
    // Компоненты shadcn/ui экспортируют вместе с компонентом и cva-варианты (так устроен upstream).
    files: ['src/components/ui/**'],
    rules: { 'react-refresh/only-export-components': 'off' },
  },
  {
    files: ['**/*.test.{ts,tsx}', 'src/test/**', 'src/mocks/**'],
    rules: {
      '@typescript-eslint/no-unsafe-assignment': 'off',
      '@typescript-eslint/no-unsafe-member-access': 'off',
      'react-refresh/only-export-components': 'off',
    },
  },
)
