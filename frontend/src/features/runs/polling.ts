import type { Run, RunState } from '@/api/types'

export const ACTIVE_STATES: readonly RunState[] = [
  'queued',
  'preparing',
  'generating',
  'validating',
  'running',
  'checking_coverage',
  'improving',
]

export const TERMINAL_STATES: readonly RunState[] = ['completed', 'failed', 'cancelled']

export function isActiveState(state: RunState): boolean {
  return ACTIVE_STATES.includes(state)
}

export function isTerminalState(state: RunState): boolean {
  return TERMINAL_STATES.includes(state)
}

/** ASSUMPTION: интервалы начальные, подбираются по нагрузке backend. */
export const POLL_ACTIVE_MS = 2_000
export const POLL_MERGE_MS = 3_000
export const POLL_RECONCILE_MS = 5_000
export const POLL_IDLE_MS = 30_000

/**
 * Через сколько миллисекунд снова спрашивать статус. `false` — не опрашивать.
 * - идёт выполнение: часто;
 * - мерж запрошен и ещё не завершён (pending/unknown): следим за итогом;
 * - прогон завершён и ещё актуален: редко, чтобы заметить stale (новые коммиты);
 * - прогон устарел, провален или отменён: опрос не нужен.
 */
export function getRunRefetchInterval(run: Run | undefined): number | false {
  if (!run) return false
  if (isActiveState(run.state)) return POLL_ACTIVE_MS
  if (run.mergeStatus === 'pending') return POLL_MERGE_MS
  if (run.mergeStatus === 'unknown') return POLL_RECONCILE_MS
  if (run.state === 'completed' && run.freshness === 'current' && run.mergeStatus !== 'merged') {
    return POLL_IDLE_MS
  }
  return false
}
