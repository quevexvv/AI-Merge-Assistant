import type { ReactNode } from 'react'

import { useSession } from '@/api/hooks'
import { ErrorNotice } from '@/components/ErrorNotice'
import { Skeleton } from '@/components/ui/skeleton'

import { LoginScreen } from './LoginScreen'

/** Пропускает дальше только вошедшего пользователя; при 401 показывает экран входа. */
export function AuthGate({ children }: { children: ReactNode }) {
  const session = useSession()
  if (session.isPending) {
    return (
      <div className="flex flex-col gap-3 py-8" role="status" aria-label="Проверяем сессию">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-40 w-full" />
      </div>
    )
  }
  if (session.isError) {
    return (
      <div className="py-8">
        <ErrorNotice error={session.error} onRetry={() => void session.refetch()} />
      </div>
    )
  }
  if (session.data === null) return <LoginScreen />
  return <>{children}</>
}
