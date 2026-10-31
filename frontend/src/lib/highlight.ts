import type { HighlighterCore, ThemedToken } from 'shiki/core'

export type CodeLang = 'python' | 'gherkin' | 'text'

/** Больше этого размера подсветку не делаем: показываем простой текст. */
export const MAX_HIGHLIGHT_CHARS = 150_000

let highlighterPromise: Promise<HighlighterCore> | undefined

// Тонкая сборка Shiki: только нужные темы и языки, движок на JS-regex (без wasm).
function getHighlighter(): Promise<HighlighterCore> {
  highlighterPromise ??= (async () => {
    const [{ createHighlighterCore }, { createJavaScriptRegexEngine }] = await Promise.all([
      import('shiki/core'),
      import('shiki/engine/javascript'),
    ])
    return createHighlighterCore({
      themes: [import('@shikijs/themes/github-light'), import('@shikijs/themes/github-dark')],
      langs: [import('@shikijs/langs/python'), import('@shikijs/langs/gherkin')],
      engine: createJavaScriptRegexEngine(),
    })
  })()
  return highlighterPromise
}

/**
 * Возвращает токены по строкам. Результат — данные, а не HTML: интерфейс собирает из них
 * React-узлы с текстом, поэтому содержимое кода не может превратиться в разметку.
 */
export async function tokenize(
  code: string,
  lang: Exclude<CodeLang, 'text'>,
): Promise<ThemedToken[][]> {
  const highlighter = await getHighlighter()
  const { tokens } = highlighter.codeToTokens(code, {
    lang,
    themes: { light: 'github-light', dark: 'github-dark' },
    defaultColor: false,
  })
  return tokens
}
