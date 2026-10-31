import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import type { Run } from '@/api/types'

import { StatusStrip } from './status'

const run: Run = {
  id: 'r1',
  repository: { id: '1', fullName: 'a/b' },
  sourceBranch: 'f',
  targetBranch: 'main',
  sourceSha: 'a'.repeat(40),
  targetSha: 'b'.repeat(40),
  mode: 'default',
  state: 'completed',
  attempt: { current: 1, max: 3 },
  freshness: 'stale',
  verdict: 'passed',
  mergeStatus: 'not_requested',
  createdAt: '2026-10-30T00:00:00Z',
  updatedAt: '2026-10-30T00:00:00Z',
}

function cell(label: string) {
  const term = screen.getByText(label, { selector: 'dt' })
  return within(term.parentElement as HTMLElement)
}

describe('StatusStrip', () => {
  it('показывает состояние, актуальность, вердикт и мерж отдельно друг от друга', () => {
    render(<StatusStrip run={run} />)
    expect(cell('Состояние').getByText('Завершён')).toBeInTheDocument()
    expect(cell('Актуальность').getByText('Устарел')).toBeInTheDocument()
    expect(cell('Вердикт').getByText('Пройден')).toBeInTheDocument()
    expect(cell('Мерж').getByText('Не запрашивался')).toBeInTheDocument()
  })

  it('«Завершён» не подразумевает «Пройден», а «Пройден» — «Актуален»', () => {
    render(<StatusStrip run={{ ...run, verdict: 'failed', freshness: 'current' }} />)
    expect(cell('Состояние').getByText('Завершён')).toBeInTheDocument()
    expect(cell('Вердикт').getByText('Не пройден')).toBeInTheDocument()
    expect(cell('Актуальность').getByText('Актуален')).toBeInTheDocument()
  })

  it('вердикт not_applicable и отсутствие вердикта различаются', () => {
    const { rerender } = render(<StatusStrip run={{ ...run, verdict: 'not_applicable' }} />)
    expect(cell('Вердикт').getByText('Не применим')).toBeInTheDocument()
    rerender(<StatusStrip run={{ ...run, verdict: null, state: 'running' }} />)
    expect(cell('Вердикт').getByText('Ещё нет')).toBeInTheDocument()
  })

  it('неизвестное значение от сервера не ломает экран', () => {
    render(<StatusStrip run={{ ...run, state: 'archived' as Run['state'] }} />)
    expect(cell('Состояние').getByText('archived')).toBeInTheDocument()
  })
})
