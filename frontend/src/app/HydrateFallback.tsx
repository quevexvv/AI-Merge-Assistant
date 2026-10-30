import { Skeleton } from '@/components/ui/skeleton'

/** Показывается, пока роутер загружает первый маршрут (в том числе ленивый чанк). */
export function HydrateFallback() {
  return (
    <div role="status" aria-label="Загрузка" className="mx-auto flex max-w-5xl flex-col gap-4 p-6">
      <Skeleton className="h-8 w-64" />
      <Skeleton className="h-40 w-full" />
    </div>
  )
}
