import { useRef, useState, type FormEvent } from 'react'
import { useNavigate, useSearchParams } from 'react-router'

import { newIdempotencyKey } from '@/api/client'
import { ApiError } from '@/api/errors'
import { useBranches, useCreateRun, useRepositories, useSession } from '@/api/hooks'
import type { Repository, RunMode } from '@/api/types'
import { ErrorNotice } from '@/components/ErrorNotice'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { NativeSelect } from '@/components/ui/native-select'
import { Skeleton } from '@/components/ui/skeleton'
import { Textarea } from '@/components/ui/textarea'
import { formatDateTime, shortSha } from '@/lib/format'
import { cn } from '@/lib/utils'

import {
  isRunFormField,
  parseMode,
  validateRunForm,
  type RunFormErrors,
  type RunFormField,
  type RunFormValues,
} from './form'
import { MODE_LABEL } from './labels'

const MODE_HELP: Record<RunMode, string> = {
  default:
    'Вы описываете, что изменилось и зачем. Ожидаемое поведение берётся из описания, код служит контекстом.',
  lazy: 'Описание не нужно: ожидания выводятся из diff. Такие сценарии помечаются как «выведенные из diff».',
}

function FieldError({ id, message }: { id: string; message: string | undefined }) {
  if (!message) return null
  return (
    <p id={id} className="text-sm text-danger">
      {message}
    </p>
  )
}

function RepositoryAccess({ repository }: { repository: Repository }) {
  return (
    <div className="flex flex-wrap gap-1.5" aria-label="Права и установка приложения">
      <Badge tone={repository.permissions.analyze ? 'success' : 'warning'}>
        Анализ: {repository.permissions.analyze ? 'доступен' : 'нет доступа'}
      </Badge>
      <Badge tone={repository.permissions.write ? 'success' : 'neutral'}>
        Запись: {repository.permissions.write ? 'доступна' : 'нет'}
      </Badge>
      <Badge tone={repository.appInstalled ? 'success' : 'neutral'}>
        GitHub App: {repository.appInstalled ? 'установлен' : 'не установлен'}
      </Badge>
    </div>
  )
}

export function CreateRunForm() {
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const session = useSession()
  const repositories = useRepositories()
  const createRun = useCreateRun()

  const [values, setValues] = useState<RunFormValues>({
    repositoryId: params.get('repo') ?? '',
    sourceBranch: params.get('source') ?? '',
    targetBranch: params.get('target') ?? '',
    mode: parseMode(params.get('mode')),
    description: '',
  })
  const [clientErrors, setClientErrors] = useState<RunFormErrors>({})
  const [serverErrors, setServerErrors] = useState<RunFormErrors>({})
  // Один ключ на одно намерение. Повтор без правок (например, после обрыва сети) безопасен;
  // любая правка формы — новое намерение, значит новый ключ.
  const keyRef = useRef(newIdempotencyKey())

  const repository = repositories.data?.find((r) => r.id === values.repositoryId)
  const branches = useBranches(values.repositoryId || undefined)

  function update<K extends RunFormField>(field: K, value: RunFormValues[K]) {
    setValues((prev) => ({ ...prev, [field]: value }))
    setClientErrors((prev) => ({ ...prev, [field]: undefined }))
    setServerErrors((prev) => ({ ...prev, [field]: undefined }))
    keyRef.current = newIdempotencyKey()
  }

  function onRepositoryChange(id: string) {
    const next = repositories.data?.find((r) => r.id === id)
    setValues((prev) => ({
      ...prev,
      repositoryId: id,
      sourceBranch: '',
      targetBranch: next?.defaultBranch ?? '',
    }))
    setClientErrors({})
    setServerErrors({})
    keyRef.current = newIdempotencyKey()
  }

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const errors = validateRunForm(values)
    setClientErrors(errors)
    if (Object.keys(errors).length > 0) return
    setServerErrors({})
    createRun.mutate(
      {
        body: {
          repositoryId: values.repositoryId,
          sourceBranch: values.sourceBranch,
          targetBranch: values.targetBranch,
          mode: values.mode,
          description: values.mode === 'default' ? values.description.trim() : null,
        },
        idempotencyKey: keyRef.current,
      },
      {
        onSuccess: (run) => void navigate(`/runs/${run.id}`),
        onError: (error) => {
          if (error instanceof ApiError && error.fields.length > 0) {
            const next: RunFormErrors = {}
            for (const f of error.fields) if (isRunFormField(f.field)) next[f.field] = f.message
            setServerErrors(next)
          }
        },
      },
    )
  }

  const errors: RunFormErrors = { ...clientErrors, ...serverErrors }
  const quota = session.data?.quota
  const submitError =
    createRun.error instanceof ApiError && createRun.error.fields.length > 0
      ? null
      : createRun.error

  if (repositories.isPending) {
    return (
      <div className="flex flex-col gap-4" role="status" aria-label="Загружаем репозитории">
        <Skeleton className="h-9 w-full" />
        <Skeleton className="h-9 w-full" />
        <Skeleton className="h-28 w-full" />
      </div>
    )
  }
  if (repositories.isError) {
    return <ErrorNotice error={repositories.error} onRetry={() => void repositories.refetch()} />
  }
  if (repositories.data.length === 0) {
    return (
      <Alert tone="info">
        <AlertDescription>
          <p>
            Нет доступных репозиториев. Поддерживаются только публичные репозитории с Python-кодом;
            проверьте, что вы вошли нужным аккаунтом GitHub.
          </p>
        </AlertDescription>
      </Alert>
    )
  }

  return (
    <form onSubmit={onSubmit} noValidate className="flex max-w-2xl flex-col gap-6">
      {quota && (
        <p className="tabular text-sm text-muted-foreground" data-testid="quota">
          Осталось прогонов: {quota.runsRemaining} из {quota.runsLimit}. Лимит обновится{' '}
          {formatDateTime(quota.resetsAt)}.
        </p>
      )}
      {quota && quota.runsRemaining === 0 && (
        <Alert tone="warning">
          <AlertDescription>
            <p>
              Квота исчерпана. Сервер, скорее всего, отклонит новый прогон до обновления лимита.
            </p>
          </AlertDescription>
        </Alert>
      )}

      <div className="flex flex-col gap-2">
        <Label htmlFor="repo">Репозиторий</Label>
        <NativeSelect
          id="repo"
          value={values.repositoryId}
          onChange={(e) => onRepositoryChange(e.target.value)}
          aria-invalid={Boolean(errors.repositoryId)}
          aria-describedby={errors.repositoryId ? 'repo-error' : undefined}
        >
          <option value="">Выберите репозиторий</option>
          {repositories.data.map((r) => (
            <option key={r.id} value={r.id}>
              {r.fullName}
            </option>
          ))}
        </NativeSelect>
        <FieldError id="repo-error" message={errors.repositoryId} />
        {repository && <RepositoryAccess repository={repository} />}
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <div className="flex flex-col gap-2">
          <Label htmlFor="source">Исходная ветка</Label>
          <NativeSelect
            id="source"
            value={values.sourceBranch}
            onChange={(e) => update('sourceBranch', e.target.value)}
            disabled={!values.repositoryId || branches.isPending}
            aria-invalid={Boolean(errors.sourceBranch)}
            aria-describedby={errors.sourceBranch ? 'source-error' : undefined}
          >
            <option value="">
              {branches.isPending && values.repositoryId ? 'Загружаем…' : 'Выберите ветку'}
            </option>
            {branches.data?.map((b) => (
              <option key={b.name} value={b.name}>
                {b.name} ({shortSha(b.sha)})
              </option>
            ))}
          </NativeSelect>
          <FieldError id="source-error" message={errors.sourceBranch} />
        </div>
        <div className="flex flex-col gap-2">
          <Label htmlFor="target">Целевая ветка</Label>
          <NativeSelect
            id="target"
            value={values.targetBranch}
            onChange={(e) => update('targetBranch', e.target.value)}
            disabled={!values.repositoryId || branches.isPending}
            aria-invalid={Boolean(errors.targetBranch)}
            aria-describedby={errors.targetBranch ? 'target-error' : undefined}
          >
            <option value="">
              {branches.isPending && values.repositoryId ? 'Загружаем…' : 'Выберите ветку'}
            </option>
            {branches.data?.map((b) => (
              <option key={b.name} value={b.name}>
                {b.name} ({shortSha(b.sha)})
              </option>
            ))}
          </NativeSelect>
          <FieldError id="target-error" message={errors.targetBranch} />
        </div>
      </div>
      {branches.isError && (
        <ErrorNotice error={branches.error} onRetry={() => void branches.refetch()} />
      )}

      <fieldset className="flex flex-col gap-2">
        <legend className="mb-2 text-sm font-medium">Режим</legend>
        <div className="grid gap-3 sm:grid-cols-2">
          {(['default', 'lazy'] as const).map((mode) => (
            <label
              key={mode}
              className={cn(
                'flex cursor-pointer items-start gap-3 rounded-lg border bg-card p-3 text-sm transition-colors',
                values.mode === mode
                  ? 'border-primary ring-[3px] ring-primary/30'
                  : 'hover:bg-accent/50',
              )}
            >
              <input
                type="radio"
                name="mode"
                value={mode}
                checked={values.mode === mode}
                onChange={() => update('mode', mode)}
                className="mt-1 accent-primary"
              />
              <span className="flex flex-col gap-1">
                <span className="font-medium">{MODE_LABEL[mode]}</span>
                <span className="text-[13px] leading-snug text-muted-foreground">
                  {MODE_HELP[mode]}
                </span>
              </span>
            </label>
          ))}
        </div>
      </fieldset>

      {values.mode === 'default' && (
        <div className="flex flex-col gap-2">
          <Label htmlFor="description">Описание изменений</Label>
          <Textarea
            id="description"
            value={values.description}
            onChange={(e) => update('description', e.target.value)}
            placeholder="Что изменилось и как это должно работать. Например: скидка 10% для заказов дороже 5000 ₽."
            aria-invalid={Boolean(errors.description)}
            aria-describedby={errors.description ? 'description-error' : undefined}
          />
          <FieldError id="description-error" message={errors.description} />
        </div>
      )}

      {submitError && <ErrorNotice error={submitError} />}

      <div className="flex items-center gap-3">
        <Button type="submit" size="lg" disabled={createRun.isPending}>
          {createRun.isPending ? 'Запускаем…' : 'Запустить прогон'}
        </Button>
        <p className="text-sm text-muted-foreground">
          Запуск занимает минуты; страницу можно закрыть.
        </p>
      </div>
    </form>
  )
}
