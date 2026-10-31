import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'

import type { MergeDecision } from '@/api/types'
import { server } from '@/mocks/server'
import { renderWithProviders } from '@/test/render'

import { MergePanel } from './MergePanel'

const decision = (allowed: boolean): MergeDecision => ({
  id: 'dec-1',
  manual: { allowed, reasons: allowed ? [] : [{ code: 'x', message: 'Причина от сервера.' }] },
  auto: {
    available: false,
    enabled: false,
    reasons: [{ code: 'y', message: 'Автомерж недоступен по серверной причине.' }],
  },
  pullRequest: { number: 7, url: 'https://github.com/demo/calc-service/pull/7' },
})

describe('MergePanel', () => {
  it('кнопка активна, когда сервер разрешил', () => {
    renderWithProviders(<MergePanel runId="run-demo-ok" decision={decision(true)} />)
    expect(screen.getByRole('button', { name: 'Выполнить мерж' })).toBeEnabled()
    expect(screen.getByText('Сервер разрешил ручной мерж.')).toBeInTheDocument()
  })

  it('кнопка заблокирована, когда сервер не разрешил, причины видны', () => {
    renderWithProviders(<MergePanel runId="run-demo-ok" decision={decision(false)} />)
    expect(screen.getByRole('button', { name: 'Выполнить мерж' })).toBeDisabled()
    expect(screen.getByText('Причина от сервера.')).toBeInTheDocument()
  })

  it('автомерж только показывается, переключателя нет', () => {
    renderWithProviders(<MergePanel runId="run-demo-ok" decision={decision(true)} />)
    expect(screen.getByText(/Автомерж: недоступен/)).toBeInTheDocument()
    expect(screen.getByText('Автомерж недоступен по серверной причине.')).toBeInTheDocument()
    expect(screen.getAllByRole('button')).toHaveLength(1)
  })

  it('ссылка на PR открывается безопасно, javascript: ссылка не становится ссылкой', () => {
    const { unmount } = renderWithProviders(<MergePanel runId="r" decision={decision(true)} />)
    const link = screen.getByRole('link', { name: /Pull request #7/ })
    expect(link).toHaveAttribute('rel', 'noopener noreferrer')
    unmount()
    renderWithProviders(
      <MergePanel
        runId="r"
        decision={{ ...decision(true), pullRequest: { number: 8, url: 'javascript:alert(1)' } }}
      />,
    )
    expect(screen.queryByRole('link')).toBeNull()
    expect(screen.getByText(/Pull request #8/)).toBeInTheDocument()
  })

  it('отправляет только намерение: id решения и режим, без процента; повтор использует тот же ключ', async () => {
    const seen: { body: unknown; key: string | null; csrf: string | null }[] = []
    server.use(
      http.post('/api/v1/runs/:runId/merge', async ({ request }) => {
        seen.push({
          body: await request.json(),
          key: request.headers.get('Idempotency-Key'),
          csrf: request.headers.get('X-CSRF-Token'),
        })
        return HttpResponse.json(
          { error: { code: 'merge_not_allowed', message: 'Нельзя.' } },
          { status: 409 },
        )
      }),
    )
    const user = userEvent.setup()
    renderWithProviders(<MergePanel runId="run-demo-ok" decision={decision(true)} />)

    for (let i = 0; i < 2; i++) {
      await user.click(screen.getByRole('button', { name: 'Выполнить мерж' }))
      const dialog = await screen.findByRole('alertdialog')
      await user.click(within(dialog).getByRole('button', { name: 'Выполнить мерж' }))
      await screen.findByText('Мерж не разрешён')
    }

    expect(seen).toHaveLength(2)
    expect(seen[0]!.body).toEqual({ decisionId: 'dec-1', mode: 'manual' })
    expect(seen[0]!.key).toBeTruthy()
    expect(seen[1]!.key).toBe(seen[0]!.key)
  })
})
