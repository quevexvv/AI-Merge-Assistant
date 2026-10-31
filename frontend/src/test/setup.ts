import '@testing-library/jest-dom/vitest'

import { cleanup, configure } from '@testing-library/react'
import { afterAll, afterEach, beforeAll } from 'vitest'

import { resetMockDb } from '@/mocks/db'
import { server } from '@/mocks/server'

// Первый переход на страницу результатов подгружает ленивый чанк (Shiki, Recharts): на холодном
// кэше или под нагрузкой это дольше стандартной секунды ожидания.
configure({ asyncUtilTimeout: 5000 })

// jsdom не реализует ResizeObserver (нужен Recharts и Radix).
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver ??= ResizeObserverStub as unknown as typeof ResizeObserver

beforeAll(() => server.listen({ onUnhandledFrame: 'error' }))
afterEach(() => {
  cleanup()
  server.resetHandlers()
  resetMockDb()
  localStorage.clear()
  document.documentElement.classList.remove('dark')
})
afterAll(() => server.close())
