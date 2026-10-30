import type {
  Branch,
  MergeDecision,
  Repository,
  Run,
  RunMode,
  RunResults,
  RunState,
} from '@/api/types'

import { resultsFor, type ScenarioKey } from './fixtures'

/**
 * In-memory «сервер» для разработки и тестов. Он играет роль backend: именно здесь считаются процент
 * покрытия, вердикт и допуск к мержу. Интерфейс получает готовые значения и ничего не пересчитывает.
 */

export const mockConfig = {
  /** Сколько длится один этап прогона. */
  stageMs: 2_500,
  /** Через сколько после запроса мерж становится выполненным. */
  mergeMs: 2_500,
  authenticated: true,
  now: () => Date.now(),
}

export const MOCK_CSRF = 'mock-csrf-token'
export const MAX_ATTEMPTS = 3

export interface MockRepo extends Repository {
  scenario: ScenarioKey
}

export const REPOS: MockRepo[] = [
  repo('r1', 'demo/calc-service', 'ok', { write: true, app: true }),
  repo('r2', 'demo/low-coverage', 'low_coverage', { write: true, app: true }),
  repo('r3', 'demo/docs-only', 'no_measurable', { write: true, app: true }),
  repo('r4', 'demo/failing-tests', 'failing_tests', { write: true, app: true }),
  repo('r5', 'demo/env-error', 'env_error', { write: true, app: true }),
  repo('r6', 'demo/stale-result', 'stale', { write: true, app: true }),
  repo('r7', 'demo/read-only', 'read_only', { write: false, app: false }),
  repo('r8', 'demo/quota-exceeded', 'quota', { write: true, app: true }),
]

function repo(
  id: string,
  fullName: string,
  scenario: ScenarioKey,
  p: { write: boolean; app: boolean },
): MockRepo {
  return {
    id,
    fullName,
    defaultBranch: 'main',
    appInstalled: p.app,
    permissions: { analyze: true, write: p.write },
    scenario,
  }
}

const BRANCH_NAMES = ['main', 'develop', 'feature/discounts', 'feature/docs']

function fakeSha(seed: string): string {
  let out = ''
  for (let block = 0; out.length < 40; block++) {
    // FNV-1a по всей строке; номер блока в затравке даёт разные блоки.
    let h = (2166136261 ^ Math.imul(block + 1, 16777619)) >>> 0
    for (let i = 0; i < seed.length; i++) {
      h ^= seed.charCodeAt(i)
      h = Math.imul(h, 16777619) >>> 0
    }
    out += h.toString(16).padStart(8, '0')
  }
  return out.slice(0, 40)
}

export function branchesOf(repositoryId: string): Branch[] {
  return BRANCH_NAMES.map((name) => ({ name, sha: fakeSha(`${repositoryId}:${name}`) }))
}

interface MockRun {
  id: string
  repo: MockRepo
  sourceBranch: string
  targetBranch: string
  mode: RunMode
  description: string | null
  createdAt: number
  mergeRequestedAt: number | null
}

const PLANS: Record<ScenarioKey, RunState[]> = {
  ok: [
    'queued',
    'preparing',
    'generating',
    'validating',
    'running',
    'checking_coverage',
    'improving',
    'validating',
    'running',
    'checking_coverage',
    'completed',
  ],
  low_coverage: [
    'queued',
    'preparing',
    'generating',
    'validating',
    'running',
    'checking_coverage',
    'improving',
    'validating',
    'running',
    'checking_coverage',
    'improving',
    'validating',
    'running',
    'checking_coverage',
    'completed',
  ],
  no_measurable: [
    'queued',
    'preparing',
    'generating',
    'validating',
    'running',
    'checking_coverage',
    'completed',
  ],
  failing_tests: [
    'queued',
    'preparing',
    'generating',
    'validating',
    'running',
    'checking_coverage',
    'completed',
  ],
  env_error: ['queued', 'preparing', 'generating', 'validating', 'running', 'failed'],
  stale: [
    'queued',
    'preparing',
    'generating',
    'validating',
    'running',
    'checking_coverage',
    'completed',
  ],
  read_only: [
    'queued',
    'preparing',
    'generating',
    'validating',
    'running',
    'checking_coverage',
    'completed',
  ],
  quota: [],
}

const runs = new Map<string, MockRun>()
const idempotency = new Map<string, string>()
let counter = 0

/**
 * Сохранение мок-данных между перезагрузками страницы. Включается только в браузере (`npm run dev:mock`),
 * в тестах не используется. Сброс: удалить ключ `ama-mock-db` из localStorage.
 */
const STORAGE_KEY = 'ama-mock-db'
let persistent = false

interface StoredRun extends Omit<MockRun, 'repo'> {
  repoId: string
}

function save(): void {
  if (!persistent) return
  try {
    const stored = {
      counter,
      runs: [...runs.values()].map(({ repo, ...rest }): StoredRun => ({
        ...rest,
        repoId: repo.id,
      })),
      idempotency: [...idempotency],
    }
    localStorage.setItem(STORAGE_KEY, JSON.stringify(stored))
  } catch {
    // Хранилище недоступно — моки продолжают работать в памяти.
  }
}

export function enableMockPersistence(): void {
  persistent = true
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return
    const stored = JSON.parse(raw) as {
      counter: number
      runs: StoredRun[]
      idempotency: [string, string][]
    }
    runs.clear()
    idempotency.clear()
    for (const { repoId, ...rest } of stored.runs) {
      const repo = REPOS.find((r) => r.id === repoId)
      if (repo) runs.set(rest.id, { ...rest, repo })
    }
    for (const [key, id] of stored.idempotency) idempotency.set(key, id)
    counter = stored.counter
  } catch {
    // Повреждённые данные игнорируем: остаются сиды.
  }
}

export function resetMockDb(): void {
  runs.clear()
  idempotency.clear()
  counter = 0
  mockConfig.stageMs = 2_500
  mockConfig.mergeMs = 2_500
  mockConfig.authenticated = true
  mockConfig.now = () => Date.now()
  seed()
}

function seed(): void {
  const now = mockConfig.now()
  const ok = REPOS[0]!
  const env = REPOS[4]!
  runs.set('run-demo-ok', {
    id: 'run-demo-ok',
    repo: ok,
    sourceBranch: 'feature/discounts',
    targetBranch: 'main',
    mode: 'default',
    description: 'Скидка 10% для заказов дороже 5000 ₽. Заказ ровно на 5000 ₽ скидки не получает.',
    createdAt: now - 3 * 3_600_000,
    mergeRequestedAt: null,
  })
  runs.set('run-demo-env', {
    id: 'run-demo-env',
    repo: env,
    sourceBranch: 'develop',
    targetBranch: 'main',
    mode: 'lazy',
    description: null,
    createdAt: now - 26 * 3_600_000,
    mergeRequestedAt: null,
  })
}

resetMockDb()

function planState(m: MockRun): { state: RunState; attempt: number } {
  const plan = PLANS[m.repo.scenario]
  const elapsed = Math.max(0, mockConfig.now() - m.createdAt)
  const index =
    mockConfig.stageMs <= 0
      ? plan.length - 1
      : Math.min(plan.length - 1, Math.floor(elapsed / mockConfig.stageMs))
  const attempt = 1 + plan.slice(0, index + 1).filter((s) => s === 'improving').length
  return { state: plan[index] ?? 'queued', attempt }
}

function mergeStatusOf(m: MockRun): Run['mergeStatus'] {
  if (m.mergeRequestedAt === null) return 'not_requested'
  return mockConfig.now() - m.mergeRequestedAt >= mockConfig.mergeMs ? 'merged' : 'pending'
}

export function toRun(m: MockRun): Run {
  const { state, attempt } = planState(m)
  const parts = resultsFor(m.repo.scenario)
  const terminal = state === 'completed' || state === 'failed'
  const failed = state === 'failed'
  return {
    id: m.id,
    repository: { id: m.repo.id, fullName: m.repo.fullName },
    sourceBranch: m.sourceBranch,
    targetBranch: m.targetBranch,
    sourceSha: fakeSha(`${m.repo.id}:${m.sourceBranch}`),
    targetSha: fakeSha(`${m.repo.id}:${m.targetBranch}`),
    mode: m.mode,
    description: m.description,
    state,
    attempt: { current: attempt, max: MAX_ATTEMPTS },
    freshness: m.repo.scenario === 'stale' && terminal ? 'stale' : 'current',
    verdict: terminal ? parts.verdict : null,
    mergeStatus: mergeStatusOf(m),
    failure: failed
      ? {
          category: 'environment',
          code: 'dependency_install_failed',
          message: 'Не удалось установить зависимости проекта. Тесты не запускались.',
          failedDuring: 'running',
        }
      : null,
    createdAt: new Date(m.createdAt).toISOString(),
    updatedAt: new Date(
      Math.max(m.createdAt, m.mergeRequestedAt ?? 0) +
        (terminal ? 0 : Math.max(0, mockConfig.now() - m.createdAt)),
    ).toISOString(),
  }
}

export function listRuns(): Run[] {
  return [...runs.values()].sort((a, b) => b.createdAt - a.createdAt).map(toRun)
}

export function getRun(id: string): MockRun | undefined {
  return runs.get(id)
}

export function createRun(input: {
  repositoryId: string
  sourceBranch: string
  targetBranch: string
  mode: RunMode
  description: string | null
  idempotencyKey: string
}): Run | undefined {
  const existing = idempotency.get(input.idempotencyKey)
  if (existing) {
    const found = runs.get(existing)
    return found ? toRun(found) : undefined
  }
  const repo = REPOS.find((r) => r.id === input.repositoryId)
  if (!repo) return undefined
  counter += 1
  const id = `run-${String(counter).padStart(4, '0')}`
  const m: MockRun = {
    id,
    repo,
    sourceBranch: input.sourceBranch,
    targetBranch: input.targetBranch,
    mode: input.mode,
    description: input.mode === 'default' ? input.description : null,
    createdAt: mockConfig.now(),
    mergeRequestedAt: null,
  }
  runs.set(id, m)
  idempotency.set(input.idempotencyKey, id)
  save()
  return toRun(m)
}

export function requestMerge(m: MockRun): void {
  m.mergeRequestedAt ??= mockConfig.now()
  save()
}

export function decisionOf(m: MockRun): MergeDecision {
  const run = toRun(m)
  const parts = resultsFor(m.repo.scenario)
  const merging = run.mergeStatus !== 'not_requested'
  const allowedByScenario = m.repo.scenario === 'ok' && !merging
  const reasons = merging
    ? [{ code: 'merge_requested', message: 'Мерж уже запрошен или выполнен.' }]
    : parts.reasons
  return {
    id: `dec-${m.id}-${merging ? 'm' : 'o'}`,
    manual: { allowed: allowedByScenario, reasons: allowedByScenario ? [] : reasons },
    auto: {
      available: false,
      enabled: false,
      reasons: [
        {
          code: 'target_not_protected',
          message:
            'Ветка назначения не защищена от мержа непроверенного кандидата, автомерж недоступен.',
        },
      ],
    },
    pullRequest:
      m.repo.scenario === 'env_error'
        ? null
        : { number: 42, url: `https://github.com/${m.repo.fullName}/pull/42` },
  }
}

export function resultsOf(m: MockRun): RunResults | undefined {
  const { state } = planState(m)
  if (state !== 'completed' && state !== 'failed') return undefined
  const parts = resultsFor(m.repo.scenario)
  return {
    runId: m.id,
    attemptId: `${m.id}-a${planState(m).attempt}`,
    scenarios: parts.scenarios,
    tests: parts.tests,
    execution: parts.execution,
    coverage: parts.coverage,
    mergeDecision: decisionOf(m),
  }
}

export function isQuotaRepo(repositoryId: string): boolean {
  return REPOS.find((r) => r.id === repositoryId)?.scenario === 'quota'
}

export function quotaOf() {
  const used = [...runs.values()].filter((r) => r.id.startsWith('run-0')).length
  return {
    runsLimit: 10,
    runsRemaining: Math.max(0, 10 - used),
    resetsAt: new Date(mockConfig.now() + 20 * 3_600_000).toISOString(),
  }
}
