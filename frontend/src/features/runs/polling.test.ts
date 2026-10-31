import { describe, expect, it } from 'vitest'

import type { Run } from '@/api/types'

import {
  getRunRefetchInterval,
  POLL_ACTIVE_MS,
  POLL_IDLE_MS,
  POLL_MERGE_MS,
  POLL_RECONCILE_MS,
} from './polling'

function run(patch: Partial<Run>): Run {
  return {
    id: 'r',
    repository: { id: '1', fullName: 'a/b' },
    sourceBranch: 'f',
    targetBranch: 'main',
    sourceSha: 'a'.repeat(40),
    targetSha: 'b'.repeat(40),
    mode: 'default',
    state: 'queued',
    attempt: { current: 1, max: 3 },
    freshness: 'current',
    verdict: null,
    mergeStatus: 'not_requested',
    createdAt: '2026-10-30T00:00:00Z',
    updatedAt: '2026-10-30T00:00:00Z',
    ...patch,
  }
}

describe('getRunRefetchInterval', () => {
  it('опрашивает часто, пока прогон выполняется', () => {
    for (const state of [
      'queued',
      'preparing',
      'generating',
      'validating',
      'running',
      'checking_coverage',
      'improving',
    ] as const) {
      expect(getRunRefetchInterval(run({ state }))).toBe(POLL_ACTIVE_MS)
    }
  })

  it('следит за мержем, пока он не завершён', () => {
    expect(getRunRefetchInterval(run({ state: 'completed', mergeStatus: 'pending' }))).toBe(
      POLL_MERGE_MS,
    )
    expect(getRunRefetchInterval(run({ state: 'completed', mergeStatus: 'unknown' }))).toBe(
      POLL_RECONCILE_MS,
    )
  })

  it('редко проверяет завершённый актуальный прогон, чтобы заметить stale', () => {
    expect(getRunRefetchInterval(run({ state: 'completed' }))).toBe(POLL_IDLE_MS)
  })

  it('останавливается, когда опрашивать больше нечего', () => {
    expect(getRunRefetchInterval(run({ state: 'completed', freshness: 'stale' }))).toBe(false)
    expect(getRunRefetchInterval(run({ state: 'completed', mergeStatus: 'merged' }))).toBe(false)
    expect(getRunRefetchInterval(run({ state: 'failed' }))).toBe(false)
    expect(getRunRefetchInterval(run({ state: 'cancelled' }))).toBe(false)
    expect(getRunRefetchInterval(undefined)).toBe(false)
  })
})
