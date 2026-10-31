import '@/index.css'

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { createBrowserRouter } from 'react-router'
import { RouterProvider } from 'react-router/dom'

import { AppProviders } from '@/app/providers'
import { createQueryClient } from '@/app/query-client'
import { routes } from '@/app/routes'

async function enableMocking() {
  // Моки включаются только командой `npm run dev:mock` (vite --mode mock).
  if (import.meta.env.MODE !== 'mock') return
  const { worker } = await import('@/mocks/browser')
  await worker.start({ onUnhandledFrame: 'bypass' })
}

const queryClient = createQueryClient()
const router = createBrowserRouter(routes)

void enableMocking().then(() => {
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <AppProviders client={queryClient}>
        <RouterProvider router={router} />
      </AppProviders>
    </StrictMode>,
  )
})
