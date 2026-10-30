import { Fragment, useEffect, useMemo, useState } from 'react'
import type { ThemedToken } from 'shiki/core'

import { MAX_HIGHLIGHT_CHARS, tokenize, type CodeLang } from '@/lib/highlight'
import { toDisplayText } from '@/lib/text'
import { cn } from '@/lib/utils'

interface Highlighted {
  text: string
  lang: CodeLang
  lines: ThemedToken[][]
}

function useHighlighted(text: string, lang: CodeLang): ThemedToken[][] | null {
  const [state, setState] = useState<Highlighted | null>(null)
  useEffect(() => {
    if (lang === 'text' || text.length > MAX_HIGHLIGHT_CHARS) return
    let cancelled = false
    tokenize(text, lang)
      .then((lines) => {
        if (!cancelled) setState({ text, lang, lines })
      })
      .catch(() => {
        // Подсветка необязательна: остаёмся на обычном тексте.
      })
    return () => {
      cancelled = true
    }
  }, [text, lang])
  return state && state.text === text && state.lang === lang ? state.lines : null
}

interface CodeBlockProps {
  code: string
  lang?: CodeLang
  /** Подпись для скринридера и вкладки фокуса. */
  label: string
  className?: string
}

/**
 * Код и логи выводятся только как текст: содержимое попадает в DOM через React-текстовые узлы.
 * Подсветка Shiki добавляет лишь цвета (CSS-переменные), но не разметку из кода.
 */
export function CodeBlock({ code, lang = 'text', label, className }: CodeBlockProps) {
  // Завершающий перевод строки не создаёт лишнюю пустую строку.
  const text = useMemo(() => toDisplayText(code).replace(/\n$/, ''), [code])
  const highlighted = useHighlighted(text, lang)
  const plainLines = useMemo(() => text.split('\n'), [text])

  const lineCount = highlighted ? highlighted.length : plainLines.length
  const gutter = Array.from({ length: lineCount }, (_, i) => i + 1).join('\n')

  return (
    <div
      tabIndex={0}
      role="region"
      aria-label={label}
      className={cn(
        'max-h-[32rem] overflow-auto rounded-md border bg-code font-mono text-[13px] leading-6 [font-variant-ligatures:none]',
        className,
      )}
    >
      <div className="flex w-max min-w-full">
        {/* Номера строк — отдельная колонка: они не попадают в копируемый текст и не читаются скринридером. */}
        <pre
          aria-hidden
          className="sticky left-0 m-0 min-w-12 border-r bg-code py-3 pr-3 pl-3 text-right text-muted-foreground opacity-80 select-none"
        >
          {gutter}
        </pre>
        <pre className="m-0 flex-1 py-3 pr-4 pl-3">
          <code>
            {highlighted
              ? highlighted.map((line, i) => (
                  <Fragment key={i}>
                    {i > 0 && '\n'}
                    {line.map((token, j) => (
                      <span key={j} className="shiki-token" style={token.htmlStyle}>
                        {token.content}
                      </span>
                    ))}
                  </Fragment>
                ))
              : plainLines.join('\n')}
          </code>
        </pre>
      </div>
    </div>
  )
}
