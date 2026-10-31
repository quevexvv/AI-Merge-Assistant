import { isRouteErrorResponse } from 'react-router'

/** Технические подробности ошибки для разработчика: показываются как обычный текст. */
export function describeErrorDetails(error: unknown): string {
  if (isRouteErrorResponse(error)) {
    const body = typeof error.data === 'string' ? `\n${error.data}` : ''
    return `${error.status} ${error.statusText}${body}`.trim()
  }
  if (error instanceof Error) {
    const head = `${error.name}: ${error.message}`
    return error.stack?.includes(error.message)
      ? error.stack
      : [head, error.stack].join('\n').trim()
  }
  if (typeof error === 'string') return error
  try {
    return JSON.stringify(error, null, 2) ?? String(error)
  } catch {
    return String(error)
  }
}
