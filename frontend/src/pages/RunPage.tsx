import { AlertTriangle, ArrowLeft, RefreshCw, WifiOff } from 'lucide-react'
import { Link, useParams } from 'react-router'

import { useRun, useRunResults } from '@/api/hooks'
import { isApiError } from '@/api/errors'
import type { Run } from '@/api/types'
import { ErrorNotice } from '@/components/ErrorNotice'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { CoverageDisclaimer, CoverageGate } from '@/features/runs/CoverageGate'
import { CoverageSection } from '@/features/runs/CoverageSection'
import { FAILURE_CATEGORY_LABEL, labelOf, MODE_LABEL } from '@/features/runs/labels'
import { LogsPanel } from '@/features/runs/LogsPanel'
import { MergePanel } from '@/features/runs/MergePanel'
import { isTerminalState } from '@/features/runs/polling'
import { RunProgress } from '@/features/runs/RunProgress'
import { ScenariosPanel } from '@/features/runs/ScenariosPanel'
import { StatusStrip } from '@/features/runs/status'
import { TestsPanel } from '@/features/runs/TestsPanel'
import { formatDateTime, plural, shortSha } from '@/lib/format'
import { toDisplayText } from '@/lib/text'

function rerunLink(run: Run): string {
  const q = new URLSearchParams({
    repo: run.repository.id,
    source: run.sourceBranch,
    target: run.targetBranch,
    mode: run.mode,
  })
  return `/runs/new?${q.toString()}`
}

function RunHeader({ run }: { run: Run }) {
  return (
    <header className="flex flex-col gap-2">
      <Link
        to="/runs"
        className="inline-flex w-fit items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="size-4" aria-hidden />
        Все прогоны
      </Link>
      <h1 className="text-2xl font-semibold tracking-tight break-all">{run.repository.fullName}</h1>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-muted-foreground">
        <span className="font-mono text-[13px] break-all text-foreground">
          {run.sourceBranch} <span className="font-sans">→</span> {run.targetBranch}
        </span>
        <span
          className="font-mono text-xs"
          title="Зафиксированные коммиты: исходная → целевая ветка"
        >
          {shortSha(run.sourceSha)} → {shortSha(run.targetSha)}
        </span>
        <Badge tone="outline">{labelOf(MODE_LABEL, run.mode)}</Badge>
        <span>Создан {formatDateTime(run.createdAt)}</span>
      </div>
      {run.description && (
        <details className="text-sm">
          <summary className="w-fit cursor-pointer text-muted-foreground hover:text-foreground">
            Описание изменений
          </summary>
          <p className="mt-2 max-w-3xl leading-relaxed whitespace-pre-wrap">
            {toDisplayText(run.description)}
          </p>
        </details>
      )}
    </header>
  )
}

function Results({ run }: { run: Run }) {
  const results = useRunResults(run)

  if (results.isPending && results.fetchStatus !== 'idle') {
    return (
      <div className="flex flex-col gap-3" role="status" aria-label="Загружаем результаты">
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-48 w-full" />
      </div>
    )
  }
  if (results.isError) {
    if (isApiError(results.error) && results.error.code === 'results_not_ready') {
      return (
        <p className="text-sm text-muted-foreground">
          Результатов у этого прогона нет: он остановился до получения тестов или отчётов.
        </p>
      )
    }
    return <ErrorNotice error={results.error} onRetry={() => void results.refetch()} />
  }
  const data = results.data
  if (!data) return null

  const ran = data.execution?.status === 'passed' || data.execution?.status === 'failed'
  const counts = ran ? data.execution?.counts : undefined
  return (
    <Tabs defaultValue="summary">
      <TabsList aria-label="Разделы результатов">
        <TabsTrigger value="summary">Итог</TabsTrigger>
        <TabsTrigger value="scenarios">Сценарии ({data.scenarios.length})</TabsTrigger>
        <TabsTrigger value="tests">Тесты ({data.tests.length})</TabsTrigger>
        <TabsTrigger value="coverage">Покрытие по файлам</TabsTrigger>
        <TabsTrigger value="logs">Логи</TabsTrigger>
      </TabsList>

      <TabsContent value="summary" className="flex flex-col gap-6">
        <div className="grid gap-6 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
          <div className="flex flex-col gap-4 rounded-lg border bg-card p-5">
            <CoverageGate coverage={data.coverage} />
            <CoverageDisclaimer />
          </div>
          <div className="rounded-lg border bg-card p-5">
            <MergePanel runId={run.id} decision={data.mergeDecision} />
          </div>
        </div>
        {counts && (
          <p className="tabular text-sm text-muted-foreground">
            Запуск: {counts.total} {plural(counts.total, ['тест', 'теста', 'тестов'])}, пройдено{' '}
            {counts.passed}, упало {counts.failed}, ошибок {counts.errors}, пропущено{' '}
            {counts.skipped}.
          </p>
        )}
      </TabsContent>
      <TabsContent value="scenarios">
        <ScenariosPanel scenarios={data.scenarios} />
      </TabsContent>
      <TabsContent value="tests">
        <TestsPanel tests={data.tests} execution={data.execution} />
      </TabsContent>
      <TabsContent value="coverage">
        <CoverageSection coverage={data.coverage} />
      </TabsContent>
      <TabsContent value="logs">
        <LogsPanel execution={data.execution} />
      </TabsContent>
    </Tabs>
  )
}

export function RunPage() {
  const { runId = '' } = useParams()
  const query = useRun(runId)
  const run = query.data

  if (query.isPending) {
    return (
      <div className="flex flex-col gap-4" role="status" aria-label="Загружаем прогон">
        <Skeleton className="h-8 w-72" />
        <Skeleton className="h-16 w-full" />
        <Skeleton className="h-40 w-full" />
      </div>
    )
  }
  if (!run) {
    if (isApiError(query.error, 404)) {
      return (
        <div className="flex max-w-md flex-col gap-3 py-8">
          <h1 className="text-xl font-semibold">Прогон не найден</h1>
          <p className="text-sm text-muted-foreground">
            Такого прогона нет, либо он вам недоступен.
          </p>
          <div>
            <Button asChild variant="outline">
              <Link to="/runs">К списку прогонов</Link>
            </Button>
          </div>
        </div>
      )
    }
    return <ErrorNotice error={query.error} onRetry={() => void query.refetch()} />
  }

  const terminal = isTerminalState(run.state)

  return (
    <div className="flex flex-col gap-6">
      <title>{`${run.repository.fullName} · прогон · AI-Merge-Assistant`}</title>
      <RunHeader run={run} />
      <StatusStrip run={run} />

      {query.isRefetchError && (
        <Alert tone="warning">
          <WifiOff aria-hidden />
          <AlertTitle>Не удалось обновить статус</AlertTitle>
          <AlertDescription>
            <p>Показаны последние полученные данные. Повторяем запрос автоматически.</p>
          </AlertDescription>
        </Alert>
      )}

      {run.freshness === 'stale' && (
        <Alert tone="warning">
          <RefreshCw aria-hidden />
          <AlertTitle>Результат устарел</AlertTitle>
          <AlertDescription>
            <p>
              Ветки или тесты изменились после этого прогона. Результат относится к зафиксированным
              коммитам и не даёт права на мерж актуального кода.
            </p>
            <Button asChild variant="outline" size="sm" className="mt-1">
              <Link to={rerunLink(run)}>Запустить заново</Link>
            </Button>
          </AlertDescription>
        </Alert>
      )}

      {run.failure && (
        <Alert tone="danger">
          <AlertTriangle aria-hidden />
          <AlertTitle>{labelOf(FAILURE_CATEGORY_LABEL, run.failure.category)}</AlertTitle>
          <AlertDescription>
            <p>{toDisplayText(run.failure.message)}</p>
            <Button asChild variant="outline" size="sm" className="mt-1">
              <Link to={rerunLink(run)}>Запустить заново</Link>
            </Button>
          </AlertDescription>
        </Alert>
      )}

      {terminal ? (
        <>
          <details className="group">
            <summary className="w-fit cursor-pointer text-sm text-muted-foreground hover:text-foreground">
              Ход выполнения
            </summary>
            <div className="mt-3">
              <RunProgress run={run} />
            </div>
          </details>
          {run.state !== 'cancelled' && <Results run={run} />}
        </>
      ) : (
        <RunProgress run={run} />
      )}
    </div>
  )
}
