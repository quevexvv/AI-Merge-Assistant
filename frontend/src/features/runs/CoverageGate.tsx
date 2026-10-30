import { CheckCircle2, CircleHelp, MinusCircle, XCircle } from 'lucide-react'

import type { Coverage } from '@/api/types'
import { Badge } from '@/components/ui/badge'
import { COVERAGE_DISCLAIMER } from '@/constants/disclaimer'
import { plural } from '@/lib/format'
import { cn } from '@/lib/utils'

import { getCoverageView } from './coverage-view'

export function CoverageDisclaimer({ className }: { className?: string }) {
  return (
    <p
      data-testid="coverage-disclaimer"
      className={cn('border-l-2 pl-3 text-sm leading-relaxed text-muted-foreground', className)}
    >
      {COVERAGE_DISCLAIMER}
    </p>
  )
}

/**
 * «Ворота покрытия»: шкала с отметкой порога. Число, порог и вывод «достигнут / не достигнут»
 * приходят от сервера; клиент ничего не считает и не округляет вверх.
 */
export function CoverageGate({ coverage }: { coverage: Coverage | null }) {
  const view = getCoverageView(coverage)

  if (view.kind === 'not_applicable') {
    return (
      <div className="flex flex-col gap-3">
        <GateHeader
          badge={
            <Badge tone="neutral">
              <MinusCircle />
              Не определено
            </Badge>
          }
        />
        <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
          <span className="text-4xl font-semibold tracking-tight" data-testid="coverage-value">
            N/A
          </span>
          <span className="text-sm text-muted-foreground">нет измеряемых строк</span>
        </div>
        <div
          role="img"
          aria-label="Покрытие не определено: нет измеряемых строк. Это не 100 %."
          className="h-3 w-full rounded-full border"
          style={{
            backgroundImage:
              'repeating-linear-gradient(135deg, var(--border) 0 4px, transparent 4px 8px)',
          }}
        />
        <p className="text-sm text-muted-foreground">
          В изменениях нет исполняемых строк Python, которые можно измерить. Это не 100&nbsp;%:
          покрытие просто не определено.
        </p>
      </div>
    )
  }

  if (view.kind === 'unavailable') {
    return (
      <div className="flex flex-col gap-3">
        <GateHeader
          badge={
            <Badge tone="warning">
              <CircleHelp />
              Нет данных
            </Badge>
          }
        />
        <span className="text-4xl font-semibold tracking-tight" data-testid="coverage-value">
          —
        </span>
        <p className="text-sm text-muted-foreground">
          Отчёта о покрытии нет или он повреждён, поэтому процент показать нельзя.
        </p>
      </div>
    )
  }

  const badge =
    view.meetsThreshold === true ? (
      <Badge tone="success">
        <CheckCircle2 />
        Порог достигнут
      </Badge>
    ) : view.meetsThreshold === false ? (
      <Badge tone="danger">
        <XCircle />
        Порог не достигнут
      </Badge>
    ) : (
      <Badge tone="neutral">
        <CircleHelp />
        Решение не получено
      </Badge>
    )

  return (
    <div className="flex flex-col gap-3">
      <GateHeader badge={badge} />
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
        <span
          className="tabular text-4xl font-semibold tracking-tight"
          data-testid="coverage-value"
        >
          {view.percentText}
        </span>
        <span className="tabular text-sm text-muted-foreground">
          {view.covered} из {view.executable} изменённых исполняемых{' '}
          {plural(view.executable, ['строки', 'строк', 'строк'])}
        </span>
      </div>
      <div
        role="img"
        aria-label={`Покрытие изменённых строк: ${view.percentText}. Порог: ${view.thresholdText}.`}
        className="relative h-3 w-full rounded-full bg-muted"
      >
        <div
          className="h-full rounded-full bg-viz-covered"
          style={{ width: `${view.fillPercent}%` }}
          data-testid="coverage-fill"
        />
        <div
          className="absolute top-1/2 h-6 w-0.5 -translate-y-1/2 rounded-full bg-foreground"
          style={{ left: `${view.thresholdPercent}%` }}
          data-testid="coverage-threshold"
        />
      </div>
      <p className="text-xs text-muted-foreground">
        Чёрточка на шкале — порог {view.thresholdText}, заданный сервером.
        {view.missing !== null && view.missing > 0 && <> Не покрыто строк: {view.missing}.</>}
      </p>
    </div>
  )
}

function GateHeader({ badge }: { badge: React.ReactNode }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h3 className="text-sm font-semibold">Покрытие изменённых строк</h3>
      {badge}
    </div>
  )
}
