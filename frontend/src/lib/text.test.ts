import { describe, expect, it } from 'vitest'

import { safeHttpUrl, toDisplayText } from './text'

describe('toDisplayText', () => {
  it('приводит переводы строк к \\n', () => {
    expect(toDisplayText('a\r\nb\rc')).toBe('a\nb\nc')
  })

  it('делает видимыми ANSI-последовательности и NUL', () => {
    expect(toDisplayText('\u001b[31mFAIL\u001b[0m')).toBe('␛[31mFAIL␛[0m')
    expect(toDisplayText('a\u0000b')).toBe('a␀b')
  })

  it('делает видимыми BiDi-переопределения и невидимые символы', () => {
    expect(toDisplayText('a\u202Eb')).toBe('a<U+202E>b')
    expect(toDisplayText('a\u200Bb')).toBe('a<U+200B>b')
  })

  it('не трогает табуляцию, переводы строк и кириллицу', () => {
    expect(toDisplayText('\tтест\n')).toBe('\tтест\n')
  })

  it('не интерпретирует разметку', () => {
    expect(toDisplayText('<img src=x onerror=alert(1)>')).toBe('<img src=x onerror=alert(1)>')
  })
})

describe('safeHttpUrl', () => {
  it('пропускает только http(s)', () => {
    expect(safeHttpUrl('https://github.com/a/b/pull/1')).toBe('https://github.com/a/b/pull/1')
    expect(safeHttpUrl('javascript:alert(1)')).toBeNull()
    expect(safeHttpUrl('data:text/html,x')).toBeNull()
    expect(safeHttpUrl('не ссылка')).toBeNull()
    expect(safeHttpUrl(null)).toBeNull()
  })
})
