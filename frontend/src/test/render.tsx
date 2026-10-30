import { QueryClientProvider } from '@tanstack/react-query'
import { render } from '@testing-library/react'
import type { ReactElement } from 'react'
import { createMemoryRouter, RouterProvider, type RouteObject } from 'react-router'

import { createQueryClient } from '@/app/query-client'
import { ThemeProvider } from '@/components/ThemeProvider'

/** Рендер в провайдерах приложения. Для компонентов, которым нужен роутер, передайте `route`. */
export function renderWithProviders(ui: ReactElement, options: { route?: string } = {}) {
  const queryClient = createQueryClient()
  // Тесты не должны ждать повторов по сети.
  queryClient.setDefaultOptions({
    queries: { retry: false, staleTime: 0 },
    mutations: { retry: false },
  })
  const routes: RouteObject[] = [{ path: '*', element: ui }]
  const router = createMemoryRouter(routes, { initialEntries: [options.route ?? '/'] })
  const result = render(
    <ThemeProvider>
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    </ThemeProvider>,
  )
  return { ...result, queryClient, router }
}

/** Рендер всего приложения (маршруты, оболочка, AuthGate) по адресу. */
export function renderApp(initialPath: string, routes: RouteObject[]) {
  const queryClient = createQueryClient()
  queryClient.setDefaultOptions({
    queries: { retry: false, staleTime: 0 },
    mutations: { retry: false },
  })
  const router = createMemoryRouter(routes, { initialEntries: [initialPath] })
  const result = render(
    <ThemeProvider>
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    </ThemeProvider>,
  )
  return { ...result, queryClient, router }
}
