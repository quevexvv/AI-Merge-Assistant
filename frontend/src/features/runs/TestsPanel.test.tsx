import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import type { Execution } from '@/api/types'

import { TestsPanel } from './TestsPanel'

const base: Execution = {
  status: 'environment_error',
  exitCode: 1,
  imageDigest: null,
  counts: { total: 0, passed: 0, failed: 0, errors: 0, skipped: 0 },
  tests: [],
  logs: { text: '', truncated: false },
}

describe('TestsPanel', () => {
  it('при ошибке окружения не пишет «Всего 0 тестов», а говорит, что тесты не выполнялись', () => {
    render(<TestsPanel tests={[]} execution={base} />)
    expect(screen.queryByText(/Всего 0/)).not.toBeInTheDocument()
    expect(screen.getByText(/Ни один тест не был выполнен/)).toBeInTheDocument()
  })

  it('когда тесты выполнялись, показывает счётчики', () => {
    render(
      <TestsPanel
        tests={[]}
        execution={{
          ...base,
          status: 'failed',
          counts: { total: 5, passed: 3, failed: 1, errors: 0, skipped: 1 },
        }}
      />,
    )
    expect(screen.getByText(/Всего 5 тестов: пройдено 3, упало 1/)).toBeInTheDocument()
    expect(screen.queryByText(/Ни один тест не был выполнен/)).not.toBeInTheDocument()
  })

  it('без запуска пишет «Тесты не запускались»', () => {
    render(<TestsPanel tests={[]} execution={null} />)
    expect(screen.getByText('Тесты не запускались.')).toBeInTheDocument()
  })
})
