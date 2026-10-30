import { useEffect } from 'react'
import { useRouteError } from 'react-router'

import { ErrorNotice } from '@/components/ErrorNotice'
import { toDisplayText } from '@/lib/text'

import { describeErrorDetails } from './error-details'

export function RouteError() {
  const error = useRouteError()

  // Без этого в консоли остаётся только общее сообщение React Router.
  useEffect(() => {
    console.error('Ошибка маршрута:', error)
  }, [error])

  return (
    <div className="mx-auto flex max-w-2xl flex-col gap-3 p-6">
      <ErrorNotice error={error} />
      <details className="rounded-md border bg-muted/40 px-3 py-2 text-sm">
        <summary className="cursor-pointer font-medium select-none">Технические детали</summary>
        <pre className="mt-2 max-h-72 overflow-auto font-mono text-xs break-words whitespace-pre-wrap text-muted-foreground">
          {toDisplayText(describeErrorDetails(error))}
        </pre>
      </details>
    </div>
  )
}
