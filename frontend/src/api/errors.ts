export interface FieldError {
  field: string
  message: string
}

/** Ошибка, пришедшая от API в согласованном формате ErrorResponse. */
export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly fields: FieldError[]
  readonly retryAfterSeconds: number | undefined

  constructor(init: {
    status: number
    code: string
    message: string
    fields?: FieldError[] | undefined
    retryAfterSeconds?: number | undefined
  }) {
    super(init.message)
    this.name = 'ApiError'
    this.status = init.status
    this.code = init.code
    this.fields = init.fields ?? []
    this.retryAfterSeconds = init.retryAfterSeconds
  }
}

/** Запрос не дошёл до сервера или ответ не удалось получить. */
export class NetworkError extends Error {
  constructor(message = 'Нет связи с сервером') {
    super(message)
    this.name = 'NetworkError'
  }
}

export function isApiError(error: unknown, status?: number): error is ApiError {
  return error instanceof ApiError && (status === undefined || error.status === status)
}

interface ParsedBody {
  code?: string
  message?: string
  fields?: FieldError[]
  retryAfterSeconds?: number
}

function parseErrorBody(body: unknown): ParsedBody {
  if (typeof body !== 'object' || body === null || !('error' in body)) return {}
  const inner = body.error
  if (typeof inner !== 'object' || inner === null) return {}
  const e = inner as Record<string, unknown>
  const out: ParsedBody = {}
  if (typeof e.code === 'string') out.code = e.code
  if (typeof e.message === 'string') out.message = e.message
  if (typeof e.retryAfterSeconds === 'number') out.retryAfterSeconds = e.retryAfterSeconds
  if (Array.isArray(e.fields)) {
    out.fields = e.fields.flatMap((f: unknown) => {
      if (typeof f !== 'object' || f === null) return []
      const r = f as Record<string, unknown>
      return typeof r.field === 'string' && typeof r.message === 'string'
        ? [{ field: r.field, message: r.message }]
        : []
    })
  }
  return out
}

export function toApiError(response: Response, body: unknown): ApiError {
  const parsed = parseErrorBody(body)
  const headerRetry = Number(response.headers.get('Retry-After'))
  return new ApiError({
    status: response.status,
    code: parsed.code ?? `http_${response.status}`,
    message: parsed.message ?? `Сервер вернул ошибку ${response.status}`,
    fields: parsed.fields,
    retryAfterSeconds:
      parsed.retryAfterSeconds ??
      (Number.isFinite(headerRetry) && headerRetry > 0 ? headerRetry : undefined),
  })
}

export interface ErrorDescription {
  title: string
  message: string
}

/** Текст ошибки для пользователя. Сообщение сервера выводится как есть (это безопасный текст). */
export function describeError(error: unknown): ErrorDescription {
  if (error instanceof NetworkError) {
    return {
      title: 'Нет связи с сервером',
      message: 'Проверьте подключение к интернету и повторите попытку.',
    }
  }
  if (error instanceof ApiError) {
    switch (error.code) {
      case 'unauthorized':
        return { title: 'Нужно войти', message: 'Сессия закончилась. Войдите через GitHub снова.' }
      case 'forbidden':
        return { title: 'Недостаточно прав', message: error.message }
      case 'not_found':
        return { title: 'Не найдено', message: 'Такого объекта нет или он вам недоступен.' }
      case 'quota_exceeded':
        return {
          title: 'Квота исчерпана',
          message: withRetry(error.message, error.retryAfterSeconds),
        }
      case 'rate_limited':
        return {
          title: 'Слишком много запросов',
          message: withRetry(error.message, error.retryAfterSeconds),
        }
      case 'stale_snapshot':
        return {
          title: 'Результат устарел',
          message: 'Ветки изменились после прогона. Запустите новый прогон.',
        }
      case 'merge_not_allowed':
        return { title: 'Мерж не разрешён', message: error.message }
      case 'validation_error':
        return { title: 'Проверьте поля формы', message: error.message }
      default:
        break
    }
    if (error.status >= 500) {
      return {
        title: 'Ошибка на сервере',
        message: 'Повторите попытку позже. Если ошибка повторяется, сообщите команде.',
      }
    }
    return { title: 'Запрос отклонён', message: error.message }
  }
  return { title: 'Что-то пошло не так', message: 'Обновите страницу и повторите действие.' }
}

function withRetry(message: string, seconds: number | undefined): string {
  if (seconds === undefined) return message
  const minutes = Math.ceil(seconds / 60)
  return `${message} Повторить можно примерно через ${minutes} мин.`
}
