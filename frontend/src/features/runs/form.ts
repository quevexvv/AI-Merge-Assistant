import type { RunMode } from '@/api/types'

export interface RunFormValues {
  repositoryId: string
  sourceBranch: string
  targetBranch: string
  mode: RunMode
  description: string
}

export type RunFormField = keyof RunFormValues
export type RunFormErrors = Partial<Record<RunFormField, string>>

/**
 * Быстрые проверки до отправки. Сервер остаётся главным: его ошибки полей показываются так же.
 * Правило режимов (team-guide, раздел 1): default требует описание изменений, lazy обходится без него.
 */
export function validateRunForm(values: RunFormValues): RunFormErrors {
  const errors: RunFormErrors = {}
  if (!values.repositoryId) errors.repositoryId = 'Выберите репозиторий.'
  if (!values.sourceBranch) errors.sourceBranch = 'Выберите исходную ветку.'
  if (!values.targetBranch) errors.targetBranch = 'Выберите целевую ветку.'
  if (values.sourceBranch && values.sourceBranch === values.targetBranch) {
    errors.targetBranch = 'Исходная и целевая ветки должны отличаться.'
  }
  if (values.mode === 'default' && values.description.trim() === '') {
    errors.description = 'Опишите изменения: в режиме «С описанием» это обязательно.'
  }
  return errors
}

export const RUN_FORM_FIELDS: readonly RunFormField[] = [
  'repositoryId',
  'sourceBranch',
  'targetBranch',
  'mode',
  'description',
]

export function isRunFormField(value: string): value is RunFormField {
  return (RUN_FORM_FIELDS as readonly string[]).includes(value)
}

export function parseMode(value: string | null): RunMode {
  return value === 'lazy' ? 'lazy' : 'default'
}
