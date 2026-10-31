import { MutationCache, QueryCache, QueryClient } from '@tanstack/react-query'

import { isApiError } from '@/api/errors'
import { queryKeys } from '@/api/keys'

export function createQueryClient(): QueryClient {
  const client: QueryClient = new QueryClient({
    queryCache: new QueryCache({
      onError: (error, query) => handleUnauthorized(client, error, query.queryKey),
    }),
    mutationCache: new MutationCache({
      onError: (error) => handleUnauthorized(client, error),
    }),
    defaultOptions: {
      queries: {
        // Повторяем только сетевые сбои: ответ сервера (4xx/5xx) повтор не исправит.
        retry: (count, error) => !isApiError(error) && count < 2,
        staleTime: 5_000,
        refetchOnWindowFocus: true,
      },
      mutations: { retry: false },
    },
  })
  return client
}

/** Любой 401 → перечитываем сессию; AuthGate покажет экран входа. */
function handleUnauthorized(client: QueryClient, error: unknown, key?: readonly unknown[]) {
  if (!isApiError(error, 401)) return
  if (key?.[0] === queryKeys.session[0]) return
  void client.invalidateQueries({ queryKey: queryKeys.session })
}
