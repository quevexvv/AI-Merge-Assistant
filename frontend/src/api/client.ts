import createClient, { type Middleware } from 'openapi-fetch'

import { uuidV4 } from '@/lib/uuid'

import { NetworkError, toApiError } from './errors'
import type { paths } from './schema'

let csrfToken: string | null = null

/** Токен приходит в Session и добавляется ко всем небезопасным запросам. */
export function setCsrfToken(token: string | null): void {
  csrfToken = token
}

const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS'])

const csrfMiddleware: Middleware = {
  onRequest({ request }) {
    if (csrfToken && !SAFE_METHODS.has(request.method.toUpperCase())) {
      request.headers.set('X-CSRF-Token', csrfToken)
    }
    return request
  },
}

// Абсолютный URL нужен, чтобы Request работал и в браузере, и в тестах (Node).
export const API_BASE_PATH = '/api/v1'

export const api = createClient<paths>({
  baseUrl: new URL(API_BASE_PATH, window.location.origin).toString().replace(/\/$/, ''),
  credentials: 'same-origin',
  // fetch берётся в момент вызова, а не при создании клиента: так его можно подменить (MSW в тестах).
  fetch: (request) => globalThis.fetch(request),
})
api.use(csrfMiddleware)

interface FetchResult<D> {
  data?: D | undefined
  error?: unknown
  response: Response
}

/** Превращает результат openapi-fetch в данные либо бросает ApiError / NetworkError. */
export async function unwrap<D>(request: Promise<FetchResult<D>>): Promise<D> {
  let result: FetchResult<D>
  try {
    result = await request
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === 'AbortError') throw cause
    throw new NetworkError()
  }
  if (result.error !== undefined || result.data === undefined) {
    throw toApiError(result.response, result.error)
  }
  return result.data
}

/** Ключ идемпотентности: один на одно намерение пользователя. */
export function newIdempotencyKey(): string {
  return uuidV4()
}

export function githubLoginUrl(returnTo: string): string {
  const url = new URL(`${API_BASE_PATH}/auth/github/login`, window.location.origin)
  // Только относительный путь внутри приложения.
  if (returnTo.startsWith('/') && !returnTo.startsWith('//'))
    url.searchParams.set('returnTo', returnTo)
  return `${url.pathname}${url.search}`
}
