import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'

import type { RunResults } from '@/api/types'
import { COVERAGE_DISCLAIMER } from '@/constants/disclaimer'
import { createRun, mockConfig } from '@/mocks/db'
import { server } from '@/mocks/server'
import { renderApp } from '@/test/render'

import { routes } from './routes'

/** Создаёт в моке завершённый прогон выбранного сценария и возвращает его id. */
function seedFinished(repositoryId: string): string {
  mockConfig.stageMs = 0
  const run = createRun({
    repositoryId,
    sourceBranch: 'feature/discounts',
    targetBranch: 'main',
    mode: 'lazy',
    description: null,
    idempotencyKey: `seed-${repositoryId}-0001`,
  })
  return run!.id
}

const mergeButton = () => screen.getByRole('button', { name: 'Выполнить мерж' })

describe('создание прогона → прогресс → результаты', () => {
  it('проходит вертикаль целиком, включая опрос статуса', async () => {
    const user = userEvent.setup()
    const { router } = renderApp('/runs/new', routes)

    await user.selectOptions(await screen.findByLabelText('Репозиторий'), 'r1')
    // Ветки подгружаются после выбора репозитория; целевая ветка подставляется из defaultBranch.
    await waitFor(() => expect(screen.getByLabelText('Исходная ветка')).toBeEnabled())
    await user.selectOptions(screen.getByLabelText('Исходная ветка'), 'feature/discounts')
    expect(screen.getByLabelText('Целевая ветка')).toHaveValue('main')
    await user.type(
      screen.getByLabelText('Описание изменений'),
      'Скидка 10% для заказов дороже 5000 ₽',
    )
    await user.click(screen.getByRole('button', { name: 'Запустить прогон' }))

    // Прогресс: прогон принят и стоит в очереди.
    expect(await screen.findByRole('heading', { name: 'demo/calc-service' })).toBeInTheDocument()
    expect(router.state.location.pathname).toMatch(/^\/runs\/run-\d+$/)
    expect(await screen.findByRole('heading', { name: 'В очереди' })).toBeInTheDocument()
    expect(screen.getByText(/Страница обновляется сама/)).toBeInTheDocument()

    // Сервер «дорабатывает» прогон; очередной опрос приносит результат без перезагрузки.
    mockConfig.stageMs = 0
    expect(await screen.findByText('92,8', { exact: false }, { timeout: 6000 })).toBeInTheDocument()
    expect(screen.getByTestId('coverage-disclaimer').textContent).toBe(COVERAGE_DISCLAIMER)
    expect(mergeButton()).toBeEnabled()
  }, 20_000)

  it('не отправляет форму без обязательных полей и не требует описание в режиме lazy', async () => {
    const user = userEvent.setup()
    renderApp('/runs/new', routes)
    await screen.findByLabelText('Репозиторий')

    await user.click(screen.getByRole('button', { name: 'Запустить прогон' }))
    expect(await screen.findByText('Выберите репозиторий.')).toBeInTheDocument()
    expect(screen.getByText('Выберите исходную ветку.')).toBeInTheDocument()
    expect(screen.getByText(/в режиме «С описанием» это обязательно/)).toBeInTheDocument()

    await user.click(screen.getByRole('radio', { name: /По diff \(lazy\)/ }))
    expect(screen.queryByLabelText('Описание изменений')).toBeNull()
    await user.click(screen.getByRole('button', { name: 'Запустить прогон' }))
    expect(screen.queryByText(/в режиме «С описанием» это обязательно/)).toBeNull()
  })

  it('показывает ошибку квоты от сервера и права репозитория', async () => {
    const user = userEvent.setup()
    renderApp('/runs/new', routes)
    await user.selectOptions(await screen.findByLabelText('Репозиторий'), 'r8')
    await waitFor(() => expect(screen.getByLabelText('Исходная ветка')).toBeEnabled())
    expect(screen.getByText('Запись: доступна')).toBeInTheDocument()
    await user.selectOptions(screen.getByLabelText('Исходная ветка'), 'develop')
    await user.click(screen.getByRole('radio', { name: /По diff \(lazy\)/ }))
    await user.click(screen.getByRole('button', { name: 'Запустить прогон' }))
    expect(await screen.findByText('Квота исчерпана')).toBeInTheDocument()
    expect(screen.getByText(/Дневная квота прогонов исчерпана/)).toBeInTheDocument()
  })

  it('показывает ограничения репозитория только для чтения', async () => {
    const user = userEvent.setup()
    renderApp('/runs/new', routes)
    await user.selectOptions(await screen.findByLabelText('Репозиторий'), 'r7')
    expect(screen.getByText('Запись: нет')).toBeInTheDocument()
    expect(screen.getByText('GitHub App: не установлен')).toBeInTheDocument()
    expect(screen.getByText('Анализ: доступен')).toBeInTheDocument()
  })
})

describe('результаты и покрытие', () => {
  it('успешный прогон: покрытие, порог, сценарии, тесты, логи', async () => {
    const user = userEvent.setup()
    renderApp('/runs/run-demo-ok', routes)
    expect(await screen.findByTestId('coverage-value')).toHaveTextContent('92,8')
    expect(screen.getByText('Порог достигнут')).toBeInTheDocument()
    expect(mergeButton()).toBeEnabled()

    await user.click(screen.getByRole('tab', { name: /Сценарии/ }))
    expect(await screen.findByText('Скидка 10% для заказов дороже 5000 ₽')).toBeInTheDocument()
    expect(screen.getByText('Из описания')).toBeInTheDocument()
    // Выведенный из diff сценарий помечен и сопровождается предупреждением.
    expect(screen.getByText('Выведено из diff')).toBeInTheDocument()

    await user.click(screen.getByRole('tab', { name: /Тесты/ }))
    expect(await screen.findByText('ai-tests/test_discounts.py')).toBeInTheDocument()

    await user.click(screen.getByRole('tab', { name: 'Покрытие по файлам' }))
    expect(await screen.findByText('calc/cart.py')).toBeInTheDocument()
    expect(screen.getByText('58\u201359, 72')).toBeInTheDocument()
    expect(screen.getByTestId('coverage-disclaimer').textContent).toBe(COVERAGE_DISCLAIMER)
  })

  it('покрытие ниже порога: число сервера, «Порог не достигнут», мерж заблокирован с причиной', async () => {
    renderApp(`/runs/${seedFinished('r2')}`, routes)
    expect(await screen.findByTestId('coverage-value')).toHaveTextContent('73,8')
    expect(screen.getByText('Порог не достигнут')).toBeInTheDocument()
    expect(mergeButton()).toBeDisabled()
    expect(screen.getByText('Покрытие изменённых строк ниже порога 90 %.')).toBeInTheDocument()
  })

  it('нет измеряемых строк: N/A, а не 100 %; мерж заблокирован', async () => {
    renderApp(`/runs/${seedFinished('r3')}`, routes)
    const value = await screen.findByTestId('coverage-value')
    expect(value).toHaveTextContent('N/A')
    expect(value).not.toHaveTextContent('100')
    expect(mergeButton()).toBeDisabled()
    expect(screen.getByText('Не применим')).toBeInTheDocument()
  })

  it('упавшие тесты: вердикт «Не пройден», результат теста и лог как текст', async () => {
    const user = userEvent.setup()
    renderApp(`/runs/${seedFinished('r4')}`, routes)
    await screen.findByTestId('coverage-value')
    const verdict = screen.getByText('Вердикт', { selector: 'dt' }).parentElement as HTMLElement
    expect(within(verdict).getByText('Не пройден')).toBeInTheDocument()

    await user.click(screen.getByRole('tab', { name: /Тесты/ }))
    expect(await screen.findByText('Упал')).toBeInTheDocument()
    expect(screen.getByText(/возможный дефект в calc\/discounts\.py/)).toBeInTheDocument()

    await user.click(screen.getByRole('tab', { name: 'Логи' }))
    const log = await screen.findByLabelText('Лог запуска тестов')
    // ESC из ANSI-цветов виден как символ, а не исполняется/скрывается.
    expect(log.textContent).toContain('␛[31mFAILED')
    expect(log.textContent).not.toContain('\u001b')
  })

  it('ошибка окружения: причина и категория, покрытие «—», никакого ложного успеха', async () => {
    renderApp('/runs/run-demo-env', routes)
    expect(await screen.findByText('Ошибка окружения')).toBeInTheDocument()
    expect(screen.getByText(/Не удалось установить зависимости/)).toBeInTheDocument()
    expect(await screen.findByTestId('coverage-value')).toHaveTextContent('—')
    expect(mergeButton()).toBeDisabled()
    const merge = screen.getByText('Мерж', { selector: 'dt' }).parentElement as HTMLElement
    expect(within(merge).getByText('Не запрашивался')).toBeInTheDocument()
  })

  it('устаревший результат: «Устарел» показан отдельно от вердикта «Пройден»; мерж заблокирован', async () => {
    renderApp(`/runs/${seedFinished('r6')}`, routes)
    expect(await screen.findByText('Результат устарел')).toBeInTheDocument()
    const freshness = screen.getByText('Актуальность', { selector: 'dt' })
      .parentElement as HTMLElement
    const verdict = screen.getByText('Вердикт', { selector: 'dt' }).parentElement as HTMLElement
    expect(within(freshness).getByText('Устарел')).toBeInTheDocument()
    expect(within(verdict).getByText('Пройден')).toBeInTheDocument()
    // 92,8 % и «Пройден», но сервер не разрешает — кнопка заблокирована.
    expect(await screen.findByTestId('coverage-value')).toHaveTextContent('92,8')
    expect(mergeButton()).toBeDisabled()
    expect(screen.getByRole('link', { name: 'Запустить заново' })).toHaveAttribute(
      'href',
      expect.stringContaining('/runs/new?repo=r6'),
    )
  })
})

describe('кнопка мержа следует только серверному решению', () => {
  function withDecision(
    allowed: boolean,
    patchCoverage: Partial<NonNullable<RunResults['coverage']>>,
  ) {
    server.use(
      http.get('/api/v1/runs/run-demo-ok/results', () => {
        return HttpResponse.json(buildResults(allowed, patchCoverage))
      }),
    )
  }

  function buildResults(
    allowed: boolean,
    patchCoverage: Partial<NonNullable<RunResults['coverage']>>,
  ): RunResults {
    return {
      runId: 'run-demo-ok',
      attemptId: 'a1',
      scenarios: [],
      tests: [],
      execution: null,
      coverage: {
        status: 'measured',
        percent: 100,
        coveredChangedLines: 10,
        executableChangedLines: 10,
        missingChangedLines: 0,
        thresholdPercent: 90,
        meetsThreshold: true,
        files: [],
        ...patchCoverage,
      },
      mergeDecision: {
        id: 'dec-x',
        manual: {
          allowed,
          reasons: allowed ? [] : [{ code: 'r', message: 'Запрещено сервером.' }],
        },
        auto: { available: false, enabled: false, reasons: [] },
        pullRequest: null,
      },
    }
  }

  it('100 % покрытия, но сервер запретил → кнопка заблокирована', async () => {
    withDecision(false, {})
    renderApp('/runs/run-demo-ok', routes)
    expect(await screen.findByTestId('coverage-value')).toHaveTextContent('100')
    expect(mergeButton()).toBeDisabled()
    expect(screen.getByText('Запрещено сервером.')).toBeInTheDocument()
  })

  it('покрытие ниже порога, но сервер разрешил → кнопка активна (клиент не перепроверяет)', async () => {
    withDecision(true, {
      percent: 10,
      coveredChangedLines: 1,
      executableChangedLines: 10,
      missingChangedLines: 9,
      meetsThreshold: false,
    })
    renderApp('/runs/run-demo-ok', routes)
    expect(await screen.findByTestId('coverage-value')).toHaveTextContent('10')
    expect(mergeButton()).toBeEnabled()
  })

  it('мерж: подтверждение → статус «Выполняется», повторное нажатие невозможно', async () => {
    const user = userEvent.setup()
    renderApp('/runs/run-demo-ok', routes)
    await screen.findByTestId('coverage-value')
    await user.click(mergeButton())
    const dialog = await screen.findByRole('alertdialog')
    await user.click(within(dialog).getByRole('button', { name: 'Выполнить мерж' }))

    expect(await screen.findByText(/Запрос на мерж принят/)).toBeInTheDocument()
    const merge = screen.getByText('Мерж', { selector: 'dt' }).parentElement as HTMLElement
    await waitFor(() => expect(within(merge).getByText('Выполняется')).toBeInTheDocument())
    // Сервер сменил решение (мерж уже запрошен) — кнопка заблокирована.
    await waitFor(() => expect(mergeButton()).toBeDisabled())
  })
})

describe('вход, список и ошибки', () => {
  it('без сессии показывает вход через GitHub с возвратом на текущую страницу', async () => {
    mockConfig.authenticated = false
    renderApp('/runs', routes)
    const link = await screen.findByRole('link', { name: /Войти через GitHub/ })
    expect(link).toHaveAttribute('href', '/api/v1/auth/github/login?returnTo=%2Fruns')
    expect(screen.queryByText('Прогоны', { selector: 'h1' })).toBeNull()
  })

  it('401 посреди работы возвращает на экран входа', async () => {
    const { queryClient } = renderApp('/runs', routes)
    expect(await screen.findByRole('heading', { name: 'Прогоны' })).toBeInTheDocument()
    mockConfig.authenticated = false
    await queryClient.invalidateQueries({ queryKey: ['runs'] })
    expect(await screen.findByRole('link', { name: /Войти через GitHub/ })).toBeInTheDocument()
  })

  it('список показывает только ваши прогоны с раздельными статусами', async () => {
    renderApp('/runs', routes)
    expect(await screen.findByText('demo/calc-service')).toBeInTheDocument()
    expect(screen.getByText('demo/env-error')).toBeInTheDocument()
    expect(screen.getByText('Ошибка')).toBeInTheDocument()
  })

  it('пустой список приглашает запустить первый прогон', async () => {
    server.use(http.get('/api/v1/runs', () => HttpResponse.json({ items: [], nextCursor: null })))
    renderApp('/runs', routes)
    expect(await screen.findByText('Прогонов пока нет')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Запустить первый прогон/ })).toBeInTheDocument()
  })

  it('чужой или несуществующий прогон: нейтральное сообщение без деталей', async () => {
    renderApp('/runs/run-chuzhoy', routes)
    expect(await screen.findByRole('heading', { name: 'Прогон не найден' })).toBeInTheDocument()
  })

  it('ошибка сервера при загрузке прогона предлагает повторить', async () => {
    server.use(
      http.get('/api/v1/runs/:id', () =>
        HttpResponse.json({ error: { code: 'internal_error', message: 'x' } }, { status: 500 }),
      ),
    )
    renderApp('/runs/run-demo-ok', routes)
    expect(await screen.findByText('Ошибка на сервере')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Повторить' })).toBeInTheDocument()
  })

  it('неизвестный адрес показывает страницу «не найдена»', async () => {
    renderApp('/нет-такой', routes)
    expect(await screen.findByRole('heading', { name: 'Страница не найдена' })).toBeInTheDocument()
  })
})
