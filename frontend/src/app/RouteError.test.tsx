import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { describeErrorDetails } from './error-details'
import { RouteError } from './RouteError'

function Boom(): never {
  throw new TypeError('crypto.randomUUID is not a function')
}

describe('RouteError', () => {
  beforeEach(() => {
    // React и роутер шумят в консоль при ожидаемой ошибке рендера.
    vi.spyOn(console, 'error').mockImplementation(() => {})
  })
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('показывает понятный текст и прячет технические детали под раскрывашкой', async () => {
    const user = userEvent.setup()
    const router = createMemoryRouter([{ path: '/', Component: Boom, ErrorBoundary: RouteError }])
    render(<RouterProvider router={router} />)

    expect(screen.getByText('Что-то пошло не так')).toBeInTheDocument()
    const summary = screen.getByText('Технические детали')
    await user.click(summary)
    expect(screen.getByText(/TypeError: crypto\.randomUUID is not a function/)).toBeVisible()
  })

  it('пишет ошибку в консоль', () => {
    const router = createMemoryRouter([{ path: '/', Component: Boom, ErrorBoundary: RouteError }])
    render(<RouterProvider router={router} />)
    expect(console.error).toHaveBeenCalledWith('Ошибка маршрута:', expect.any(TypeError))
  })
})

describe('describeErrorDetails', () => {
  it('обрабатывает строки и произвольные значения', () => {
    expect(describeErrorDetails('просто текст')).toBe('просто текст')
    expect(describeErrorDetails({ a: 1 })).toContain('"a": 1')
  })

  it('не падает на циклических объектах', () => {
    const loop: Record<string, unknown> = {}
    loop.self = loop
    expect(typeof describeErrorDetails(loop)).toBe('string')
  })
})
