import type {
  Execution,
  FailureCategory,
  Freshness,
  MergeStatus,
  RunMode,
  RunState,
  Scenario,
  TestResult,
  Verdict,
} from '@/api/types'

/** Подпись из словаря; неизвестное значение от сервера показываем как есть, а не ломаем экран. */
export function labelOf<K extends string>(dict: Record<K, string>, key: string): string {
  return (dict as Record<string, string>)[key] ?? key
}

export const STATE_LABEL: Record<RunState, string> = {
  queued: 'В очереди',
  preparing: 'Подготовка',
  generating: 'Генерация тестов',
  validating: 'Проверка тестов',
  running: 'Запуск тестов',
  checking_coverage: 'Проверка покрытия',
  improving: 'Доработка тестов',
  completed: 'Завершён',
  failed: 'Ошибка',
  cancelled: 'Отменён',
}

export const STATE_HINT: Record<RunState, string> = {
  queued: 'Прогон принят и ждёт свободного исполнителя.',
  preparing: 'Фиксируем коммиты веток и готовим окружение.',
  generating: 'Модель пишет сценарии и pytest-тесты.',
  validating: 'Статический шлюз проверяет сгенерированные файлы.',
  running: 'Тесты выполняются в изолированном окружении.',
  checking_coverage: 'Считаем покрытие добавленных и изменённых строк.',
  improving: 'Покрытие ниже порога: дописываем тесты по непокрытым строкам.',
  completed: 'Выполнение завершено. Допуск к мержу определяет сервер.',
  failed: 'Прогон остановился с ошибкой.',
  cancelled: 'Прогон отменён.',
}

export const FRESHNESS_LABEL: Record<Freshness, string> = {
  current: 'Актуален',
  stale: 'Устарел',
}

export const VERDICT_LABEL: Record<Verdict, string> = {
  passed: 'Пройден',
  failed: 'Не пройден',
  not_applicable: 'Не применим',
}

export const MERGE_STATUS_LABEL: Record<MergeStatus, string> = {
  not_requested: 'Не запрашивался',
  pending: 'Выполняется',
  merged: 'Выполнен',
  conflict: 'Конфликт',
  failed: 'Не удался',
  unknown: 'Итог неизвестен',
}

export const MODE_LABEL: Record<RunMode, string> = {
  default: 'С описанием',
  lazy: 'По diff (lazy)',
}

export const FAILURE_CATEGORY_LABEL: Record<FailureCategory, string> = {
  model: 'Ошибка модели',
  environment: 'Ошибка окружения',
  tests: 'Ошибка тестов',
  quota: 'Квота',
  permissions: 'Права доступа',
  stale_snapshot: 'Устаревший снимок',
  internal: 'Внутренняя ошибка',
}

export const ORIGIN_LABEL: Record<Scenario['origin'], string> = {
  requirements: 'Из описания',
  inferred: 'Выведено из diff',
}

export const OUTCOME_LABEL: Record<TestResult['outcome'], string> = {
  passed: 'Пройден',
  failed: 'Упал',
  error: 'Ошибка',
  skipped: 'Пропущен',
}

export const EXECUTION_STATUS_LABEL: Record<Execution['status'], string> = {
  passed: 'Тесты прошли',
  failed: 'Есть упавшие тесты',
  collect_error: 'Ошибка сбора тестов',
  environment_error: 'Ошибка окружения',
  timeout: 'Превышено время',
}
