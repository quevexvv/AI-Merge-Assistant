import type { Execution, TestFile } from '@/api/types'
import { CodeBlock } from '@/components/CodeBlock'
import { Badge } from '@/components/ui/badge'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { formatDuration, plural } from '@/lib/format'
import { toDisplayText } from '@/lib/text'

import { labelOf, OUTCOME_LABEL } from './labels'

const OUTCOME_TONE = {
  passed: 'success',
  failed: 'danger',
  error: 'danger',
  skipped: 'neutral',
} as const

export function TestsPanel({
  tests,
  execution,
}: {
  tests: TestFile[]
  execution: Execution | null
}) {
  return (
    <div className="flex flex-col gap-6">
      {execution ? (
        <section className="flex flex-col gap-3">
          <h3 className="text-sm font-semibold">Результаты запуска</h3>
          {execution.counts.total > 0 || execution.tests.length > 0 ? (
            <p className="tabular text-sm text-muted-foreground">
              Всего {execution.counts.total}{' '}
              {plural(execution.counts.total, ['тест', 'теста', 'тестов'])}: пройдено{' '}
              {execution.counts.passed}, упало {execution.counts.failed}, ошибок{' '}
              {execution.counts.errors}, пропущено {execution.counts.skipped}.
            </p>
          ) : (
            <p className="text-sm text-muted-foreground">
              Ни один тест не был выполнен — результатов тестов нет.
            </p>
          )}
          {execution.tests.length > 0 && (
            <Table>
              <caption className="sr-only">Результаты отдельных тестов</caption>
              <TableHeader>
                <TableRow>
                  <TableHead>Тест</TableHead>
                  <TableHead>Результат</TableHead>
                  <TableHead className="text-right">Время</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {execution.tests.map((t) => (
                  <TableRow key={t.nodeId}>
                    <TableCell className="min-w-0">
                      <div className="font-mono text-xs break-all">{toDisplayText(t.nodeId)}</div>
                      {t.message && (
                        <pre className="mt-1 font-mono text-xs break-words whitespace-pre-wrap text-muted-foreground">
                          {toDisplayText(t.message)}
                        </pre>
                      )}
                    </TableCell>
                    <TableCell>
                      <Badge tone={OUTCOME_TONE[t.outcome] ?? 'neutral'}>
                        {labelOf(OUTCOME_LABEL, t.outcome)}
                      </Badge>
                    </TableCell>
                    <TableCell className="tabular text-right whitespace-nowrap">
                      {formatDuration(t.durationMs)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </section>
      ) : (
        <p className="text-sm text-muted-foreground">Тесты не запускались.</p>
      )}

      <section className="flex flex-col gap-3">
        <h3 className="text-sm font-semibold">Сгенерированные файлы</h3>
        {tests.length === 0 ? (
          <p className="text-sm text-muted-foreground">Файлов с тестами нет.</p>
        ) : (
          <ul className="flex flex-col gap-4">
            {tests.map((file) => (
              <li key={file.path} className="flex min-w-0 flex-col gap-1.5">
                <div className="flex flex-wrap items-baseline gap-x-3">
                  <span className="font-mono text-sm break-all">{toDisplayText(file.path)}</span>
                  <span className="font-mono text-xs text-muted-foreground" title="SHA-256 файла">
                    {file.sha256.slice(0, 12)}
                  </span>
                </div>
                <CodeBlock code={file.content} lang="python" label={`Тесты: ${file.path}`} />
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  )
}
