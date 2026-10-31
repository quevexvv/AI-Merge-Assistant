import { describe, expect, it } from 'vitest'

import { formatLineRanges, formatPercent, plural } from './format'

const nbsp = '\u00A0'

describe('formatPercent', () => {
  it('показывает число сервера с десятыми', () => {
    expect(formatPercent(92.857142)).toBe(`92,8${nbsp}%`)
    expect(formatPercent(90)).toBe(`90${nbsp}%`)
  })

  it('усекает, а не округляет вверх: 89,96 не превращается в 90', () => {
    expect(formatPercent(89.96)).toBe(`89,9${nbsp}%`)
    expect(formatPercent(99.99)).toBe(`99,9${nbsp}%`)
  })

  it('не теряет значения из-за плавающей точки', () => {
    expect(formatPercent(57.7)).toBe(`57,7${nbsp}%`)
    expect(formatPercent(4.35)).toBe(`4,3${nbsp}%`)
  })

  it('null и не-числа показываются как N/A, а не 0 и не 100', () => {
    expect(formatPercent(null)).toBe('N/A')
    expect(formatPercent(undefined)).toBe('N/A')
    expect(formatPercent(Number.NaN)).toBe('N/A')
  })
})

describe('formatLineRanges', () => {
  it('сворачивает подряд идущие строки', () => {
    expect(formatLineRanges([1, 2, 3, 7, 9, 10])).toBe('1–3, 7, 9–10')
    expect(formatLineRanges([])).toBe('')
    expect(formatLineRanges([5, 3, 4, 3])).toBe('3–5')
  })
})

describe('plural', () => {
  it('склоняет по-русски', () => {
    const f = ['тест', 'теста', 'тестов'] as const
    expect([1, 2, 5, 11, 21, 22, 25].map((n) => plural(n, f))).toEqual([
      'тест',
      'теста',
      'тестов',
      'тестов',
      'тест',
      'теста',
      'тестов',
    ])
  })
})
