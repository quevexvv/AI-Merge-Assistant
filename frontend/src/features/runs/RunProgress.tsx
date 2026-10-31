import { Check, Loader2, X } from 'lucide-react'

import type { Run, RunState } from '@/api/types'
import { cn } from '@/lib/utils'

import { labelOf, STATE_HINT, STATE_LABEL } from './labels'

const PIPELINE: RunState[] = [
  'queued',
  'preparing',
  'generating',
  'validating',
  'running',
  'checking_coverage',
  'improving',
  'completed',
]

type StepStatus = 'done' | 'current' | 'pending' | 'failed'

function visibleSteps(run: Run): RunState[] {
  // «Доработка» — не обязательный этап: показываем, только если она была или идёт.
  const showImproving =
    run.state === 'improving' ||
    run.attempt.current > 1 ||
    run.failure?.failedDuring === 'improving'
  return PIPELINE.filter((s) => s !== 'improving' || showImproving)
}

function stepStatuses(run: Run, steps: RunState[]): StepStatus[] {
  const index = (s: RunState | undefined) => (s ? steps.indexOf(s) : -1)
  if (run.state === 'completed') return steps.map(() => 'done')
  if (run.state === 'failed' || run.state === 'cancelled') {
    const at = index(run.failure?.failedDuring)
    return steps.map((_, i) =>
      at < 0 ? 'pending' : i < at ? 'done' : i === at ? 'failed' : 'pending',
    )
  }
  const current = index(run.state)
  return steps.map((_, i) => (i < current ? 'done' : i === current ? 'current' : 'pending'))
}

export function RunProgress({ run }: { run: Run }) {
  const steps = visibleSteps(run)
  const statuses = stepStatuses(run, steps)
  const terminal = run.state === 'completed' || run.state === 'failed' || run.state === 'cancelled'

  return (
    <section aria-labelledby="progress-heading" className="flex flex-col gap-4">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h2 id="progress-heading" className="text-base font-semibold">
          {labelOf(STATE_LABEL, run.state)}
        </h2>
        <span className="tabular text-sm text-muted-foreground">
          Попытка {run.attempt.current} из {run.attempt.max}
        </span>
      </div>
      <p className="text-sm text-muted-foreground">{labelOf(STATE_HINT, run.state)}</p>
      <ol className="grid grid-cols-1 gap-1 sm:grid-cols-[repeat(auto-fit,minmax(9rem,1fr))] sm:gap-2">
        {steps.map((step, i) => {
          const status = statuses[i] ?? 'pending'
          return (
            <li
              key={step}
              aria-current={status === 'current' ? 'step' : undefined}
              className={cn(
                'flex items-center gap-2 rounded-md border px-3 py-2 text-sm',
                status === 'current' &&
                  'border-primary bg-accent font-medium text-accent-foreground',
                status === 'done' && 'text-foreground',
                status === 'pending' && 'border-dashed text-muted-foreground',
                status === 'failed' && 'border-danger/50 bg-danger-soft font-medium text-danger',
              )}
            >
              <span aria-hidden className="inline-flex size-4 shrink-0 items-center justify-center">
                {status === 'done' && <Check className="size-4 text-success" />}
                {status === 'current' && (
                  <Loader2 className="size-4 animate-spin motion-reduce:animate-none" />
                )}
                {status === 'failed' && <X className="size-4" />}
              </span>
              <span>{labelOf(STATE_LABEL, step)}</span>
              <span className="sr-only">
                {status === 'done' && ' — выполнено'}
                {status === 'current' && ' — выполняется'}
                {status === 'failed' && ' — остановлено с ошибкой'}
                {status === 'pending' && ' — ещё не начато'}
              </span>
            </li>
          )
        })}
      </ol>
      {!terminal && (
        <p className="text-xs text-muted-foreground">
          Страница обновляется сама. Её можно закрыть: прогон продолжится на сервере.
        </p>
      )}
    </section>
  )
}
