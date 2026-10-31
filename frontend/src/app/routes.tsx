import { Navigate, type RouteObject } from 'react-router'

import { NewRunPage } from '@/pages/NewRunPage'
import { NotFoundPage } from '@/pages/NotFoundPage'
import { RunsPage } from '@/pages/RunsPage'

import { AppLayout } from './AppLayout'
import { HydrateFallback } from './HydrateFallback'
import { RouteError } from './RouteError'

export const routes: RouteObject[] = [
  {
    path: '/',
    Component: AppLayout,
    ErrorBoundary: RouteError,
    HydrateFallback,
    children: [
      { index: true, element: <Navigate to="/runs" replace /> },
      { path: 'runs', Component: RunsPage },
      { path: 'runs/new', Component: NewRunPage },
      {
        path: 'runs/:runId',
        // Страница результатов тянет Shiki и Recharts — грузим отдельным чанком.
        lazy: async () => ({ Component: (await import('@/pages/RunPage')).RunPage }),
      },
      { path: '*', Component: NotFoundPage },
    ],
  },
]
