import { AlertTriangle } from 'lucide-react'

import type { Coverage } from '@/api/types'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { formatLineRanges } from '@/lib/format'
import { toDisplayText } from '@/lib/text'

import { CoverageChart } from './CoverageChart'
import { CoverageDisclaimer } from './CoverageGate'
import { getFilePercentText } from './coverage-view'

const FILE_STATUS_LABEL = {
  measured: 'Измерен',
  not_applicable: 'N/A',
  missing_report: 'Нет в отчёте',
} as const

/** Покрытие по файлам: таблица (основной вид) и график (дополнение). */
export function CoverageSection({ coverage }: { coverage: Coverage | null }) {
  const files = coverage?.files ?? []
  const missingFiles = files.filter((f) => f.status === 'missing_report')

  return (
    <div className="flex flex-col gap-5">
      <CoverageDisclaimer />
      {missingFiles.length > 0 && (
        <Alert tone="danger">
          <AlertTriangle aria-hidden />
          <AlertTitle>Часть изменённых файлов не попала в отчёт</AlertTitle>
          <AlertDescription>
            <p>
              Для {missingFiles.length} файлов нет данных о покрытии. Это не 0&nbsp;% и не N/A:
              отчёт неполный, поэтому результату нельзя доверять.
            </p>
          </AlertDescription>
        </Alert>
      )}
      {files.length === 0 ? (
        <p className="text-sm text-muted-foreground">Покрытие по файлам недоступно.</p>
      ) : (
        <>
          <CoverageChart files={files} />
          <Table>
            <caption className="sr-only">Покрытие изменённых строк по файлам</caption>
            <TableHeader>
              <TableRow>
                <TableHead>Файл</TableHead>
                <TableHead>Статус</TableHead>
                <TableHead className="text-right">Покрыто / измеряемо</TableHead>
                <TableHead className="text-right">Покрытие</TableHead>
                <TableHead>Непокрытые строки</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {files.map((file) => (
                <TableRow key={file.path}>
                  <TableCell className="max-w-72 font-mono text-xs break-all">
                    {toDisplayText(file.path)}
                  </TableCell>
                  <TableCell>
                    <Badge tone={file.status === 'missing_report' ? 'danger' : 'neutral'}>
                      {FILE_STATUS_LABEL[file.status] ?? file.status}
                    </Badge>
                  </TableCell>
                  <TableCell className="tabular text-right whitespace-nowrap">
                    {file.status === 'missing_report'
                      ? '—'
                      : `${file.covered} / ${file.executable}`}
                  </TableCell>
                  <TableCell className="tabular text-right whitespace-nowrap">
                    {getFilePercentText(file)}
                  </TableCell>
                  <TableCell className="font-mono text-xs">
                    {file.missingLines.length > 0 ? formatLineRanges(file.missingLines) : '—'}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </>
      )}
    </div>
  )
}
