import { AlertTriangle } from 'lucide-react'

import { describeError } from '@/api/errors'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'

/** Единый вид ошибки запроса: что случилось и что делать. */
export function ErrorNotice({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const { title, message } = describeError(error)
  return (
    <Alert tone="danger">
      <AlertTriangle aria-hidden />
      <AlertTitle>{title}</AlertTitle>
      <AlertDescription>
        <p>{message}</p>
        {onRetry && (
          <Button variant="outline" size="sm" onClick={onRetry} className="mt-1">
            Повторить
          </Button>
        )}
      </AlertDescription>
    </Alert>
  )
}
