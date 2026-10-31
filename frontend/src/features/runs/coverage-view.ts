import type { Coverage, CoverageFile } from '@/api/types'
import { formatPercent } from '@/lib/format'

/**
 * Что показать вместо покрытия. Процент здесь НЕ вычисляется: берётся число сервера.
 * Клиент лишь решает, можно ли показывать это число как процент.
 *
 * Правило N/A (team-guide, п. 5.4): при нуле измеряемых строк показываем N/A, а не 100 %,
 * даже если сервер по ошибке прислал число.
 */
export type CoverageView =
  | {
      kind: 'measured'
      percentText: string
      covered: number
      executable: number
      missing: number | null
      thresholdText: string
      meetsThreshold: boolean | null
      fillPercent: number
      thresholdPercent: number
    }
  | { kind: 'not_applicable' }
  | { kind: 'unavailable' }

export function getCoverageView(coverage: Coverage | null): CoverageView {
  if (!coverage) return { kind: 'unavailable' }
  const { status, percent, coveredChangedLines, executableChangedLines } = coverage
  if (status === 'not_applicable' || (status === 'measured' && executableChangedLines === 0)) {
    return { kind: 'not_applicable' }
  }
  if (
    status !== 'measured' ||
    percent === null ||
    !Number.isFinite(percent) ||
    coveredChangedLines === null ||
    executableChangedLines === null
  ) {
    return { kind: 'unavailable' }
  }
  return {
    kind: 'measured',
    percentText: formatPercent(percent),
    covered: coveredChangedLines,
    executable: executableChangedLines,
    missing: coverage.missingChangedLines,
    thresholdText: formatPercent(coverage.thresholdPercent),
    meetsThreshold: coverage.meetsThreshold,
    // Только ширина полосы для показа; значение не используется для решений.
    fillPercent: Math.min(100, Math.max(0, percent)),
    thresholdPercent: Math.min(100, Math.max(0, coverage.thresholdPercent)),
  }
}

export function getFilePercentText(file: CoverageFile): string {
  if (file.status === 'measured' && file.executable > 0 && file.percent !== null) {
    return formatPercent(file.percent)
  }
  return file.status === 'missing_report' ? '—' : 'N/A'
}
