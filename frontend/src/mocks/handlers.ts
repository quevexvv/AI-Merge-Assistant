import { http, HttpResponse } from 'msw'

import type { CreateRunRequest } from '@/api/types'

import {
  branchesOf,
  createRun,
  decisionOf,
  getRun,
  isQuotaRepo,
  listRuns,
  mockConfig,
  MOCK_CSRF,
  quotaOf,
  REPOS,
  requestMerge,
  resultsOf,
  toRun,
} from './db'

const BASE = '/api/v1'

function problem(
  status: number,
  code: string,
  message: string,
  extra: { fields?: { field: string; message: string }[]; retryAfterSeconds?: number } = {},
) {
  return HttpResponse.json({ error: { code, message, ...extra } }, { status })
}

const unauthorized = () => problem(401, 'unauthorized', 'Требуется вход.')

/** Небезопасные запросы требуют CSRF-токен и ключ идемпотентности — как договорено в контракте. */
function checkUnsafe(request: Request): Response | null {
  if (!mockConfig.authenticated) return unauthorized()
  if (request.headers.get('X-CSRF-Token') !== MOCK_CSRF) {
    return problem(403, 'forbidden', 'Недействительный CSRF-токен.')
  }
  const key = request.headers.get('Idempotency-Key')
  if (!key || key.length < 8)
    return problem(422, 'validation_error', 'Нужен заголовок Idempotency-Key.')
  return null
}

export const handlers = [
  http.get(`${BASE}/session`, () => {
    if (!mockConfig.authenticated) return unauthorized()
    return HttpResponse.json({
      user: { id: 'u1', login: 'artem-kh', name: 'Артём Х', avatarUrl: null },
      csrfToken: MOCK_CSRF,
      quota: quotaOf(),
    })
  }),

  http.get(`${BASE}/repositories`, () => {
    if (!mockConfig.authenticated) return unauthorized()
    return HttpResponse.json({
      items: REPOS.map((repo) => ({
        id: repo.id,
        fullName: repo.fullName,
        defaultBranch: repo.defaultBranch,
        appInstalled: repo.appInstalled,
        permissions: repo.permissions,
      })),
    })
  }),

  http.get(`${BASE}/repositories/:repositoryId/branches`, ({ params }) => {
    if (!mockConfig.authenticated) return unauthorized()
    const id = String(params.repositoryId)
    if (!REPOS.some((r) => r.id === id)) return problem(404, 'not_found', 'Репозиторий не найден.')
    return HttpResponse.json({ items: branchesOf(id) })
  }),

  http.get(`${BASE}/runs`, ({ request }) => {
    if (!mockConfig.authenticated) return unauthorized()
    const url = new URL(request.url)
    const limit = Number(url.searchParams.get('limit') ?? 20)
    const offset = Number(url.searchParams.get('cursor') ?? 0)
    const all = listRuns()
    const items = all.slice(offset, offset + limit)
    const next = offset + limit < all.length ? String(offset + limit) : null
    return HttpResponse.json({ items, nextCursor: next })
  }),

  http.post(`${BASE}/runs`, async ({ request }) => {
    const rejected = checkUnsafe(request)
    if (rejected) return rejected
    const body = (await request.json()) as CreateRunRequest
    const fields: { field: string; message: string }[] = []
    if (!REPOS.some((r) => r.id === body.repositoryId))
      fields.push({ field: 'repositoryId', message: 'Репозиторий недоступен.' })
    if (body.sourceBranch === body.targetBranch)
      fields.push({ field: 'targetBranch', message: 'Ветки должны отличаться.' })
    if (body.mode === 'default' && !(body.description ?? '').trim()) {
      fields.push({
        field: 'description',
        message: 'Для режима с описанием нужно описание изменений.',
      })
    }
    if (fields.length > 0)
      return problem(422, 'validation_error', 'Проверьте поля формы.', { fields })
    if (isQuotaRepo(body.repositoryId)) {
      return problem(429, 'quota_exceeded', 'Дневная квота прогонов исчерпана.', {
        retryAfterSeconds: 3600,
      })
    }
    const run = createRun({
      ...body,
      description: body.description ?? null,
      idempotencyKey: request.headers.get('Idempotency-Key') ?? '',
    })
    return run
      ? HttpResponse.json(run, { status: 202 })
      : problem(404, 'not_found', 'Репозиторий не найден.')
  }),

  http.get(`${BASE}/runs/:runId`, ({ params }) => {
    if (!mockConfig.authenticated) return unauthorized()
    const m = getRun(String(params.runId))
    return m ? HttpResponse.json(toRun(m)) : problem(404, 'not_found', 'Прогон не найден.')
  }),

  http.get(`${BASE}/runs/:runId/results`, ({ params }) => {
    if (!mockConfig.authenticated) return unauthorized()
    const m = getRun(String(params.runId))
    if (!m) return problem(404, 'not_found', 'Прогон не найден.')
    const results = resultsOf(m)
    return results
      ? HttpResponse.json(results)
      : problem(404, 'results_not_ready', 'Результатов ещё нет.')
  }),

  http.post(`${BASE}/runs/:runId/merge`, async ({ params, request }) => {
    const rejected = checkUnsafe(request)
    if (rejected) return rejected
    const m = getRun(String(params.runId))
    if (!m) return problem(404, 'not_found', 'Прогон не найден.')
    const body = (await request.json()) as { decisionId?: string }
    const decision = decisionOf(m)
    if (body.decisionId !== decision.id) {
      return problem(409, 'stale_snapshot', 'Решение о мерже изменилось. Обновите страницу.')
    }
    if (!decision.manual.allowed) {
      return problem(
        409,
        'merge_not_allowed',
        decision.manual.reasons[0]?.message ?? 'Мерж не разрешён.',
      )
    }
    requestMerge(m)
    return HttpResponse.json(toRun(m), { status: 202 })
  }),
]
