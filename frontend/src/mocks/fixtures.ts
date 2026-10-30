import type { Coverage, Execution, RunResults, Scenario, TestFile } from '@/api/types'

export type ScenarioKey =
  | 'ok'
  | 'low_coverage'
  | 'no_measurable'
  | 'failing_tests'
  | 'env_error'
  | 'stale'
  | 'read_only'
  | 'quota'

const DISCOUNTS_TEST = `import pytest

from calc.discounts import apply_discount


@pytest.mark.parametrize(
    ("total", "expected"),
    [(6000, 5400), (5000, 5000), (100, 100)],
)
def test_discount_for_large_orders(total, expected):
    assert apply_discount(total) == expected


def test_negative_total_is_rejected():
    with pytest.raises(ValueError):
        apply_discount(-1)
`

const CART_TEST = `from calc.cart import Cart


def test_cart_total_uses_discount():
    cart = Cart()
    cart.add("book", 4000)
    cart.add("pen", 2000)
    assert cart.total() == 5400
`

const SCENARIOS: Scenario[] = [
  {
    id: 'sc-1',
    title: 'Скидка 10% для заказов дороже 5000 ₽',
    origin: 'requirements',
    sourceRefs: ['описание прогона'],
    gherkin: `# language: ru
Функционал: Скидка на крупные заказы

  Сценарий: Заказ дороже 5000 ₽ получает скидку 10%
    Дано в корзине товары на сумму 6000 ₽
    Когда рассчитывается итоговая стоимость
    Тогда итоговая стоимость равна 5400 ₽

  Сценарий: Заказ ровно на 5000 ₽ скидки не получает
    Дано в корзине товары на сумму 5000 ₽
    Когда рассчитывается итоговая стоимость
    Тогда итоговая стоимость равна 5000 ₽
`,
  },
  {
    id: 'sc-2',
    title: 'Отрицательная сумма отклоняется',
    origin: 'inferred',
    sourceRefs: ['calc/discounts.py:6'],
    gherkin: `# language: ru
Функционал: Проверка входных данных

  Сценарий: Отрицательная сумма заказа вызывает ошибку
    Дано сумма заказа равна -1
    Когда рассчитывается скидка
    Тогда возникает ошибка ValueError
`,
  },
]

const TESTS: TestFile[] = [
  {
    path: 'ai-tests/test_discounts.py',
    content: DISCOUNTS_TEST,
    sha256: 'a3f1c9d2e47b8c0d51f6a9b3c2d4e5f60718293a4b5c6d7e8f9012a3b4c5d6e7',
    scenarioIds: ['sc-1', 'sc-2'],
  },
  {
    path: 'ai-tests/test_cart.py',
    content: CART_TEST,
    sha256: '9b8a7c6d5e4f30211203f4e5d6c7b8a99a8b7c6d5e4f30211203f4e5d6c7b8a9',
    scenarioIds: ['sc-1'],
  },
]

const PASSED_EXECUTION: Execution = {
  status: 'passed',
  exitCode: 0,
  imageDigest: 'sha256:5c1e0f3a9d7b2468ace13579bdf02468ace13579bdf02468ace13579bdf02468',
  counts: { total: 5, passed: 5, failed: 0, errors: 0, skipped: 0 },
  tests: [
    {
      nodeId: 'ai-tests/test_discounts.py::test_discount_for_large_orders[6000-5400]',
      outcome: 'passed',
      durationMs: 4,
      message: null,
    },
    {
      nodeId: 'ai-tests/test_discounts.py::test_discount_for_large_orders[5000-5000]',
      outcome: 'passed',
      durationMs: 3,
      message: null,
    },
    {
      nodeId: 'ai-tests/test_discounts.py::test_discount_for_large_orders[100-100]',
      outcome: 'passed',
      durationMs: 3,
      message: null,
    },
    {
      nodeId: 'ai-tests/test_discounts.py::test_negative_total_is_rejected',
      outcome: 'passed',
      durationMs: 5,
      message: null,
    },
    {
      nodeId: 'ai-tests/test_cart.py::test_cart_total_uses_discount',
      outcome: 'passed',
      durationMs: 9,
      message: null,
    },
  ],
  logs: {
    text: `============================= test session starts ==============================
platform linux -- Python 3.13.1, pytest-9.1.1
collected 5 items

ai-tests/test_discounts.py ....                                          [ 80%]
ai-tests/test_cart.py .                                                  [100%]

============================== 5 passed in 0.12s ===============================
`,
    truncated: false,
  },
}

function coverageOf(
  files: { path: string; covered: number; executable: number; missingLines: number[] }[],
  meets: boolean,
): Coverage {
  const covered = files.reduce((s, f) => s + f.covered, 0)
  const executable = files.reduce((s, f) => s + f.executable, 0)
  return {
    status: 'measured',
    // Мок играет роль сервера: процент считается здесь, а не в интерфейсе.
    percent: (100 * covered) / executable,
    coveredChangedLines: covered,
    executableChangedLines: executable,
    missingChangedLines: executable - covered,
    thresholdPercent: 90,
    meetsThreshold: meets,
    files: files.map((f) => ({
      path: f.path,
      status: 'measured' as const,
      percent: (100 * f.covered) / f.executable,
      covered: f.covered,
      executable: f.executable,
      missing: f.executable - f.covered,
      missingLines: f.missingLines,
    })),
  }
}

const COVERAGE_OK = coverageOf(
  [
    { path: 'calc/discounts.py', covered: 18, executable: 18, missingLines: [] },
    { path: 'calc/cart.py', covered: 21, executable: 24, missingLines: [58, 59, 72] },
  ],
  true,
)

const COVERAGE_LOW = coverageOf(
  [
    {
      path: 'calc/discounts.py',
      covered: 12,
      executable: 18,
      missingLines: [14, 15, 22, 23, 30, 31],
    },
    { path: 'calc/cart.py', covered: 19, executable: 24, missingLines: [44, 45, 46, 58, 59] },
  ],
  false,
)

const COVERAGE_FAILING = coverageOf(
  [
    { path: 'calc/discounts.py', covered: 16, executable: 18, missingLines: [30, 31] },
    { path: 'calc/cart.py', covered: 20, executable: 24, missingLines: [44, 45, 46, 72] },
  ],
  false,
)

const COVERAGE_NA: Coverage = {
  status: 'not_applicable',
  percent: null,
  coveredChangedLines: 0,
  executableChangedLines: 0,
  missingChangedLines: 0,
  thresholdPercent: 90,
  meetsThreshold: null,
  files: [
    {
      path: 'README.md',
      status: 'not_applicable',
      percent: null,
      covered: 0,
      executable: 0,
      missing: 0,
      missingLines: [],
    },
  ],
}

const COVERAGE_UNAVAILABLE: Coverage = {
  status: 'unavailable',
  percent: null,
  coveredChangedLines: null,
  executableChangedLines: null,
  missingChangedLines: null,
  thresholdPercent: 90,
  meetsThreshold: null,
  files: [
    {
      path: 'calc/discounts.py',
      status: 'missing_report',
      percent: null,
      covered: 0,
      executable: 0,
      missing: 0,
      missingLines: [],
    },
  ],
}

const FAILING_EXECUTION: Execution = {
  status: 'failed',
  exitCode: 1,
  imageDigest: PASSED_EXECUTION.imageDigest ?? null,
  counts: { total: 5, passed: 4, failed: 1, errors: 0, skipped: 0 },
  tests: [
    ...PASSED_EXECUTION.tests.slice(0, 2),
    {
      nodeId: 'ai-tests/test_discounts.py::test_discount_for_large_orders[100-100]',
      outcome: 'failed',
      durationMs: 6,
      message:
        'assert 90 == 100\n  где 90 = apply_discount(100)\nСкидка применена к заказу дешевле порога 5000 ₽ — возможный дефект в calc/discounts.py:9.',
    },
    ...PASSED_EXECUTION.tests.slice(3),
  ],
  logs: {
    text: `============================= test session starts ==============================
collected 5 items

ai-tests/test_discounts.py ..F.                                          [ 80%]
ai-tests/test_cart.py .                                                  [100%]

=================================== FAILURES ===================================
____________ test_discount_for_large_orders[100-100] ____________

    assert apply_discount(total) == expected
E   assert 90 == 100
E    +  where 90 = apply_discount(100)

\u001b[31mFAILED\u001b[0m ai-tests/test_discounts.py::test_discount_for_large_orders[100-100]
========================= 1 failed, 4 passed in 0.14s ==========================
`,
    truncated: false,
  },
}

const ENV_EXECUTION: Execution = {
  status: 'environment_error',
  exitCode: 1,
  imageDigest: null,
  counts: { total: 0, passed: 0, failed: 0, errors: 0, skipped: 0 },
  tests: [],
  logs: {
    text: `Collecting requirements from requirements.txt
ERROR: Could not find a version that satisfies the requirement legacy-billing==2.0 (from versions: none)
ERROR: No matching distribution found for legacy-billing==2.0
`,
    truncated: true,
  },
}

export interface ResultsParts {
  scenarios: Scenario[]
  tests: TestFile[]
  execution: Execution | null
  coverage: Coverage | null
  verdict: 'passed' | 'failed' | 'not_applicable' | null
  reasons: { code: string; message: string }[]
}

export function resultsFor(key: ScenarioKey): ResultsParts {
  switch (key) {
    case 'low_coverage':
      return {
        scenarios: SCENARIOS,
        tests: TESTS,
        execution: PASSED_EXECUTION,
        coverage: COVERAGE_LOW,
        verdict: 'failed',
        reasons: [
          {
            code: 'coverage_below_threshold',
            message: 'Покрытие изменённых строк ниже порога 90 %.',
          },
        ],
      }
    case 'no_measurable':
      return {
        scenarios: [],
        tests: [],
        execution: null,
        coverage: COVERAGE_NA,
        verdict: 'not_applicable',
        reasons: [
          {
            code: 'coverage_not_applicable',
            message:
              'Нет измеряемых строк: покрытие не определено, мерж через сервис заблокирован.',
          },
        ],
      }
    case 'failing_tests':
      return {
        scenarios: SCENARIOS,
        tests: TESTS,
        execution: FAILING_EXECUTION,
        coverage: COVERAGE_FAILING,
        verdict: 'failed',
        reasons: [
          {
            code: 'tests_failed',
            message: 'Часть тестов не прошла: возможен дефект в изменениях.',
          },
          {
            code: 'coverage_below_threshold',
            message: 'Покрытие изменённых строк ниже порога 90 %.',
          },
        ],
      }
    case 'env_error':
      return {
        scenarios: SCENARIOS,
        tests: TESTS,
        execution: ENV_EXECUTION,
        coverage: COVERAGE_UNAVAILABLE,
        verdict: null,
        reasons: [
          { code: 'execution_error', message: 'Тесты не были выполнены из-за ошибки окружения.' },
          { code: 'coverage_unavailable', message: 'Отчёта о покрытии нет.' },
        ],
      }
    case 'stale':
      return {
        scenarios: SCENARIOS,
        tests: TESTS,
        execution: PASSED_EXECUTION,
        coverage: COVERAGE_OK,
        verdict: 'passed',
        reasons: [
          {
            code: 'stale_snapshot',
            message: 'Исходная ветка изменилась после прогона. Запустите проверку заново.',
          },
        ],
      }
    case 'read_only':
      return {
        scenarios: SCENARIOS,
        tests: TESTS,
        execution: PASSED_EXECUTION,
        coverage: COVERAGE_OK,
        verdict: 'passed',
        reasons: [
          {
            code: 'no_write_permission',
            message: 'У вас нет права записи в этот репозиторий, а GitHub App не установлен.',
          },
        ],
      }
    case 'ok':
    case 'quota':
    default:
      return {
        scenarios: SCENARIOS,
        tests: TESTS,
        execution: PASSED_EXECUTION,
        coverage: COVERAGE_OK,
        verdict: 'passed',
        reasons: [],
      }
  }
}

export type { RunResults }
