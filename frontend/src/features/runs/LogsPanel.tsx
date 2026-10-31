import type { Execution } from '@/api/types'
import { CodeBlock } from '@/components/CodeBlock'
import { Badge } from '@/components/ui/badge'
import { toDisplayText } from '@/lib/text'

import { EXECUTION_STATUS_LABEL, labelOf } from './labels'

export function LogsPanel({ execution }: { execution: Execution | null }) {
  if (!execution)
    return <p className="text-sm text-muted-foreground">Запуск не выполнялся, логов нет.</p>
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
        <Badge tone={execution.status === 'passed' ? 'success' : 'danger'}>
          {labelOf(EXECUTION_STATUS_LABEL, execution.status)}
        </Badge>
        <span className="text-muted-foreground">
          Код выхода: <span className="font-mono">{execution.exitCode ?? '—'}</span>
        </span>
        {execution.imageDigest && (
          <span className="min-w-0 break-all text-muted-foreground">
            Образ: <span className="font-mono text-xs">{toDisplayText(execution.imageDigest)}</span>
          </span>
        )}
      </div>
      {execution.logs.text.length === 0 ? (
        <p className="text-sm text-muted-foreground">Вывод пуст.</p>
      ) : (
        <CodeBlock code={execution.logs.text} label="Лог запуска тестов" />
      )}
      {execution.logs.truncated && (
        <p className="text-xs text-muted-foreground">
          Лог сокращён сервером: показана только его часть.
        </p>
      )}
    </div>
  )
}
