import {
  keepPreviousData,
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query'

import { api, setCsrfToken, unwrap } from './client'
import { isApiError } from './errors'
import { queryKeys } from './keys'
import type { CreateRunRequest, Run } from './types'
import { getRunRefetchInterval, isActiveState, isTerminalState } from '@/features/runs/polling'

/** null — пользователь не вошёл (401). Это нормальное состояние, а не ошибка запроса. */
export function useSession() {
  return useQuery({
    queryKey: queryKeys.session,
    queryFn: async () => {
      try {
        const session = await unwrap(api.GET('/session'))
        setCsrfToken(session.csrfToken)
        return session
      } catch (error) {
        if (isApiError(error, 401)) {
          setCsrfToken(null)
          return null
        }
        throw error
      }
    },
    staleTime: 60_000,
  })
}

export function useRepositories() {
  return useQuery({
    queryKey: queryKeys.repositories,
    queryFn: async () => (await unwrap(api.GET('/repositories'))).items,
    staleTime: 60_000,
  })
}

export function useBranches(repositoryId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.branches(repositoryId ?? ''),
    queryFn: async () =>
      (
        await unwrap(
          api.GET('/repositories/{repositoryId}/branches', {
            params: { path: { repositoryId: repositoryId ?? '' } },
          }),
        )
      ).items,
    enabled: Boolean(repositoryId),
    staleTime: 30_000,
  })
}

export function useCreateRun() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ body, idempotencyKey }: { body: CreateRunRequest; idempotencyKey: string }) =>
      unwrap(
        api.POST('/runs', {
          body,
          params: { header: { 'Idempotency-Key': idempotencyKey } },
        }),
      ),
    onSuccess: (run) => {
      queryClient.setQueryData(queryKeys.run(run.id), run)
      void queryClient.invalidateQueries({ queryKey: queryKeys.runs, exact: true })
      void queryClient.invalidateQueries({ queryKey: queryKeys.session })
    },
  })
}

export function useRuns() {
  return useInfiniteQuery({
    queryKey: queryKeys.runs,
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) =>
      unwrap(
        api.GET('/runs', {
          params: { query: { limit: 20, ...(pageParam ? { cursor: pageParam } : {}) } },
        }),
      ),
    getNextPageParam: (last) => last.nextCursor ?? undefined,
    refetchInterval: (query) => {
      const runs = query.state.data?.pages.flatMap((p) => p.items) ?? []
      return runs.some((r) => isActiveState(r.state)) ? 5_000 : false
    },
  })
}

/** Статус прогона с автоматическим опросом (см. getRunRefetchInterval). */
export function useRun(runId: string) {
  return useQuery({
    queryKey: queryKeys.run(runId),
    queryFn: () => unwrap(api.GET('/runs/{runId}', { params: { path: { runId } } })),
    refetchInterval: (query) => getRunRefetchInterval(query.state.data),
    retry: (count, error) => !isApiError(error) && count < 3,
  })
}

/**
 * Результаты запрашиваются, когда прогон дошёл до конца (completed/failed). Версия в ключе —
 * updatedAt: если сервер изменил прогон (stale, мерж), решение о мерже перечитывается.
 */
export function useRunResults(run: Run | undefined) {
  const runId = run?.id ?? ''
  return useQuery({
    queryKey: queryKeys.results(runId, run?.updatedAt ?? ''),
    queryFn: () => unwrap(api.GET('/runs/{runId}/results', { params: { path: { runId } } })),
    enabled: Boolean(run) && isTerminalState(run!.state) && run!.state !== 'cancelled',
    placeholderData: keepPreviousData,
    retry: (count, error) => !isApiError(error) && count < 2,
  })
}

export function useRequestMerge(runId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ decisionId, idempotencyKey }: { decisionId: string; idempotencyKey: string }) =>
      unwrap(
        api.POST('/runs/{runId}/merge', {
          params: { path: { runId }, header: { 'Idempotency-Key': idempotencyKey } },
          body: { decisionId, mode: 'manual' },
        }),
      ),
    onSuccess: (run) => {
      queryClient.setQueryData(queryKeys.run(runId), run)
    },
    onError: () => {
      // 409 (stale) и подобные: перечитываем актуальное состояние.
      void queryClient.invalidateQueries({ queryKey: queryKeys.run(runId) })
    },
  })
}
