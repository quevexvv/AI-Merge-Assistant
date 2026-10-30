import {
  Ban,
  CheckCircle2,
  CircleDashed,
  CircleHelp,
  GitMerge,
  Hourglass,
  Loader2,
  MinusCircle,
  RefreshCw,
  XCircle,
  AlertTriangle,
  Clock,
} from 'lucide-react'
import type { ComponentProps, ReactNode } from 'react'

import type { Freshness, MergeStatus, Run, RunState, Verdict } from '@/api/types'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'

import { FRESHNESS_LABEL, labelOf, MERGE_STATUS_LABEL, STATE_LABEL, VERDICT_LABEL } from './labels'
import { isActiveState } from './polling'

type Tone = NonNullable<ComponentProps<typeof Badge>['tone']>

/**
 * Четыре независимых измерения прогона. Каждое — отдельный значок со своей подписью:
 * «Завершён» не означает «Пройден», «Пройден» не означает «Актуален», а «Актуален» не разрешает мерж.
 */

function StateBadge({ state }: { state: RunState }) {
  let tone: Tone = 'neutral'
  let icon: ReactNode = <CircleDashed />
  if (state === 'completed') {
    icon = <CheckCircle2 />
  } else if (state === 'failed') {
    tone = 'danger'
    icon = <XCircle />
  } else if (state === 'cancelled') {
    icon = <Ban />
  } else if (state === 'queued') {
    icon = <Clock />
  } else if (isActiveState(state)) {
    tone = 'info'
    icon = <Loader2 className="animate-spin motion-reduce:animate-none" />
  }
  return (
    <Badge tone={tone}>
      {icon}
      {labelOf(STATE_LABEL, state)}
    </Badge>
  )
}

function FreshnessBadge({ freshness }: { freshness: Freshness }) {
  const stale = freshness === 'stale'
  return (
    <Badge tone={stale ? 'warning' : 'success'}>
      {stale ? <AlertTriangle /> : <RefreshCw />}
      {labelOf(FRESHNESS_LABEL, freshness)}
    </Badge>
  )
}

function VerdictBadge({ verdict }: { verdict: Verdict | null }) {
  if (verdict === null) {
    return (
      <Badge tone="neutral">
        <CircleHelp />
        Ещё нет
      </Badge>
    )
  }
  const map: Record<Verdict, { tone: Tone; icon: ReactNode }> = {
    passed: { tone: 'success', icon: <CheckCircle2 /> },
    failed: { tone: 'danger', icon: <XCircle /> },
    not_applicable: { tone: 'neutral', icon: <MinusCircle /> },
  }
  const view = map[verdict] ?? { tone: 'neutral', icon: <CircleHelp /> }
  return (
    <Badge tone={view.tone}>
      {view.icon}
      {labelOf(VERDICT_LABEL, verdict)}
    </Badge>
  )
}

function MergeStatusBadge({ status }: { status: MergeStatus }) {
  const map: Record<MergeStatus, { tone: Tone; icon: ReactNode }> = {
    not_requested: { tone: 'neutral', icon: <CircleDashed /> },
    pending: { tone: 'info', icon: <Hourglass /> },
    merged: { tone: 'success', icon: <GitMerge /> },
    conflict: { tone: 'danger', icon: <XCircle /> },
    failed: { tone: 'danger', icon: <XCircle /> },
    unknown: { tone: 'warning', icon: <CircleHelp /> },
  }
  const view = map[status] ?? { tone: 'neutral', icon: <CircleHelp /> }
  return (
    <Badge tone={view.tone}>
      {view.icon}
      {labelOf(MERGE_STATUS_LABEL, status)}
    </Badge>
  )
}

function StatusCell({
  label,
  children,
  className,
}: {
  label: string
  children: ReactNode
  className?: string
}) {
  return (
    <div className={cn('flex min-w-0 flex-col gap-1.5 px-4 py-3', className)}>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="m-0">{children}</dd>
    </div>
  )
}

/** Полоса из четырёх раздельных значков: состояние, актуальность, вердикт, мерж. */
export function StatusStrip({ run }: { run: Run }) {
  return (
    <dl
      aria-label="Статусы прогона"
      className="grid grid-cols-2 divide-x divide-y divide-border overflow-hidden rounded-lg border bg-card md:grid-cols-4 md:divide-y-0"
    >
      <StatusCell label="Состояние">
        <StateBadge state={run.state} />
      </StatusCell>
      <StatusCell label="Актуальность">
        <FreshnessBadge freshness={run.freshness} />
      </StatusCell>
      <StatusCell label="Вердикт">
        <VerdictBadge verdict={run.verdict} />
      </StatusCell>
      <StatusCell label="Мерж">
        <MergeStatusBadge status={run.mergeStatus} />
      </StatusCell>
    </dl>
  )
}

/** Компактный вариант для списка: у каждого значка есть текстовый префикс. */
export function InlineStatuses({ run }: { run: Run }) {
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <StateBadge state={run.state} />
      {run.freshness === 'stale' && <FreshnessBadge freshness="stale" />}
      {run.verdict !== null && (
        <span className="inline-flex items-center gap-1 text-xs">
          <span className="text-muted-foreground">Вердикт:</span>
          <VerdictBadge verdict={run.verdict} />
        </span>
      )}
      {run.mergeStatus !== 'not_requested' && (
        <span className="inline-flex items-center gap-1 text-xs">
          <span className="text-muted-foreground">Мерж:</span>
          <MergeStatusBadge status={run.mergeStatus} />
        </span>
      )}
    </div>
  )
}

export { FreshnessBadge, MergeStatusBadge, StateBadge, VerdictBadge }
