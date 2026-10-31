const percentFormat = new Intl.NumberFormat('ru-RU', {
  minimumFractionDigits: 0,
  maximumFractionDigits: 1,
})

/**
 * Форматирует процент, который прислал сервер. Это только показ, не расчёт.
 * Значение усекается до десятых, а не округляется: 89,96 % не должно превращаться в «90 %»
 * рядом с порогом 90 % (округление показа не меняет решение сервера, см. team-guide, п. 5.3).
 */
export function formatPercent(percent: number | null | undefined): string {
  if (percent === null || percent === undefined || !Number.isFinite(percent)) return 'N/A'
  const truncated = Math.floor(percent * 10 + 1e-9) / 10
  return `${percentFormat.format(truncated)}\u00A0%`
}

const dateTimeFormat = new Intl.DateTimeFormat('ru-RU', { dateStyle: 'medium', timeStyle: 'short' })

export function formatDateTime(iso: string): string {
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? iso : dateTimeFormat.format(date)
}

export function shortSha(sha: string): string {
  return sha.slice(0, 7)
}

/** [1,2,3,7,9,10] → «1–3, 7, 9–10». */
export function formatLineRanges(lines: readonly number[]): string {
  const sorted = [...new Set(lines)].sort((a, b) => a - b)
  const parts: string[] = []
  let start: number | undefined
  let prev: number | undefined
  const flush = () => {
    if (start === undefined || prev === undefined) return
    parts.push(start === prev ? String(start) : `${start}–${prev}`)
  }
  for (const line of sorted) {
    if (prev !== undefined && line === prev + 1) {
      prev = line
      continue
    }
    flush()
    start = line
    prev = line
  }
  flush()
  return parts.join(', ')
}

export function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return '—'
  if (ms < 1000) return `${ms}\u00A0мс`
  return `${percentFormat.format(Math.floor(ms / 100) / 10)}\u00A0с`
}

/** Склонение: plural(5, ['тест', 'теста', 'тестов']) → «тестов». */
export function plural(n: number, forms: readonly [string, string, string]): string {
  const abs = Math.abs(n) % 100
  const last = abs % 10
  if (abs > 10 && abs < 20) return forms[2]
  if (last > 1 && last < 5) return forms[1]
  if (last === 1) return forms[0]
  return forms[2]
}
