import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import type { Coverage } from '@/api/types'
import { COVERAGE_DISCLAIMER } from '@/constants/disclaimer'

import { CoverageDisclaimer, CoverageGate } from './CoverageGate'

const measured: Coverage = {
  status: 'measured',
  percent: 92.857142,
  coveredChangedLines: 39,
  executableChangedLines: 42,
  missingChangedLines: 3,
  thresholdPercent: 90,
  meetsThreshold: true,
  files: [],
}

describe('CoverageGate', () => {
  it('показывает число сервера и счётчики, не пересчитывая их', () => {
    render(<CoverageGate coverage={measured} />)
    expect(screen.getByTestId('coverage-value')).toHaveTextContent('92,8')
    expect(screen.getByText(/39 из 42 изменённых исполняемых/)).toBeInTheDocument()
    expect(screen.getByText('Порог достигнут')).toBeInTheDocument()
  })

  it('не округляет 89,96 % до порога 90 %', () => {
    render(
      <CoverageGate
        coverage={{
          ...measured,
          percent: 89.96,
          coveredChangedLines: 8996,
          executableChangedLines: 10000,
          meetsThreshold: false,
        }}
      />,
    )
    expect(screen.getByTestId('coverage-value')).toHaveTextContent('89,9')
    expect(screen.getByText('Порог не достигнут')).toBeInTheDocument()
  })

  it('при нуле измеряемых строк показывает N/A, а не 100 %', () => {
    render(
      <CoverageGate
        coverage={{
          ...measured,
          status: 'not_applicable',
          percent: null,
          coveredChangedLines: 0,
          executableChangedLines: 0,
          missingChangedLines: 0,
          meetsThreshold: null,
        }}
      />,
    )
    expect(screen.getByTestId('coverage-value')).toHaveTextContent('N/A')
    expect(screen.getByTestId('coverage-value')).not.toHaveTextContent('100')
    expect(screen.queryByTestId('coverage-fill')).toBeNull()
    expect(screen.getByText(/Это не 100/)).toBeInTheDocument()
  })

  it('даже если сервер по ошибке прислал 100 % при нуле строк, показывает N/A', () => {
    render(
      <CoverageGate
        coverage={{
          ...measured,
          percent: 100,
          coveredChangedLines: 0,
          executableChangedLines: 0,
          missingChangedLines: 0,
        }}
      />,
    )
    expect(screen.getByTestId('coverage-value')).toHaveTextContent('N/A')
    expect(screen.queryByTestId('coverage-fill')).toBeNull()
  })

  it('без отчёта показывает прочерк и пояснение, а не число', () => {
    render(
      <CoverageGate
        coverage={{
          ...measured,
          status: 'unavailable',
          percent: null,
          coveredChangedLines: null,
          executableChangedLines: null,
          missingChangedLines: null,
          meetsThreshold: null,
        }}
      />,
    )
    expect(screen.getByTestId('coverage-value')).toHaveTextContent('—')
    expect(screen.getByText(/Отчёта о покрытии нет/)).toBeInTheDocument()
    expect(screen.getByText('Нет данных')).toBeInTheDocument()
  })

  it('coverage = null не превращается в 0 % или 100 %', () => {
    render(<CoverageGate coverage={null} />)
    expect(screen.getByTestId('coverage-value')).toHaveTextContent('—')
  })
})

describe('CoverageDisclaimer', () => {
  it('выводит согласованную надпись целиком', () => {
    render(<CoverageDisclaimer />)
    expect(screen.getByTestId('coverage-disclaimer').textContent).toBe(COVERAGE_DISCLAIMER)
  })
})
