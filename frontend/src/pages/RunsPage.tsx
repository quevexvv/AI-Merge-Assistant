import { ArrowRight, Plus } from 'lucide-react'
import { Link } from 'react-router'

import { useRuns } from '@/api/hooks'
import { ErrorNotice } from '@/components/ErrorNotice'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { MODE_LABEL, labelOf } from '@/features/runs/labels'
import { InlineStatuses } from '@/features/runs/status'
import { formatDateTime } from '@/lib/format'

export function RunsPage() {
  const runs = useRuns()
  const items = runs.data?.pages.flatMap((p) => p.items) ?? []

  return (
    <div className="flex flex-col gap-6">
      <title>Прогоны · AI-Merge-Assistant</title>
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div className="flex flex-col gap-1">
          <h1 className="text-2xl font-semibold tracking-tight">Прогоны</h1>
          <p className="text-sm text-muted-foreground">Здесь только ваши прогоны, новые сверху.</p>
        </div>
      </header>

      {runs.isPending && (
        <div className="flex flex-col gap-3" role="status" aria-label="Загружаем прогоны">
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-20 w-full" />
        </div>
      )}
      {runs.isError && <ErrorNotice error={runs.error} onRetry={() => void runs.refetch()} />}

      {runs.isSuccess && items.length === 0 && (
        <div className="flex max-w-xl flex-col items-start gap-3 rounded-lg border bg-card p-6">
          <h2 className="font-semibold">Прогонов пока нет</h2>
          <p className="text-sm text-muted-foreground">
            Выберите две ветки публичного репозитория, и сервис проверит изменения тестами.
          </p>
          <Button asChild>
            <Link to="/runs/new">
              <Plus aria-hidden />
              Запустить первый прогон
            </Link>
          </Button>
        </div>
      )}

      {items.length > 0 && (
        <ul className="flex flex-col gap-3">
          {items.map((run) => (
            <li key={run.id}>
              <Link
                to={`/runs/${run.id}`}
                className="group flex flex-col gap-2 rounded-lg border bg-card p-4 transition-colors hover:border-primary/50"
              >
                <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
                  <span className="font-semibold">{run.repository.fullName}</span>
                  <span className="text-xs text-muted-foreground">
                    {formatDateTime(run.createdAt)}
                  </span>
                </div>
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
                  <span className="font-mono text-[13px] break-all">
                    {run.sourceBranch} → {run.targetBranch}
                  </span>
                  <span className="text-muted-foreground">{labelOf(MODE_LABEL, run.mode)}</span>
                </div>
                <div className="flex items-center justify-between gap-3">
                  <InlineStatuses run={run} />
                  <ArrowRight
                    className="size-4 shrink-0 text-muted-foreground group-hover:text-foreground"
                    aria-hidden
                  />
                </div>
              </Link>
            </li>
          ))}
        </ul>
      )}

      {runs.hasNextPage && (
        <div>
          <Button
            variant="outline"
            onClick={() => void runs.fetchNextPage()}
            disabled={runs.isFetchingNextPage}
          >
            {runs.isFetchingNextPage ? 'Загружаем…' : 'Показать ещё'}
          </Button>
        </div>
      )}
    </div>
  )
}
