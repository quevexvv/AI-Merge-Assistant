import { ExternalLink, GitMerge, ShieldAlert, ShieldCheck } from 'lucide-react'
import { useRef, useState } from 'react'

import { newIdempotencyKey } from '@/api/client'
import { useRequestMerge } from '@/api/hooks'
import type { DecisionReason, MergeDecision } from '@/api/types'
import { ErrorNotice } from '@/components/ErrorNotice'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Button } from '@/components/ui/button'
import { safeHttpUrl, toDisplayText } from '@/lib/text'

function Reasons({ reasons }: { reasons: DecisionReason[] }) {
  if (reasons.length === 0) return null
  return (
    <ul className="flex list-disc flex-col gap-1 pl-5 text-sm text-muted-foreground">
      {reasons.map((r) => (
        <li key={r.code}>{toDisplayText(r.message)}</li>
      ))}
    </ul>
  )
}

interface MergePanelProps {
  runId: string
  decision: MergeDecision
}

/**
 * Панель мержа. Доступность кнопки определяется ТОЛЬКО серверным решением `decision.manual.allowed`:
 * ни процент покрытия, ни вердикт, ни актуальность на клиенте на неё не влияют.
 * Блокировка на время запроса защищает от двойного нажатия, но не является решением о допуске.
 */
export function MergePanel({ runId, decision }: MergePanelProps) {
  const merge = useRequestMerge(runId)
  const [confirmOpen, setConfirmOpen] = useState(false)
  // Один ключ на одно намерение: повторная отправка того же решения не создаёт второй мерж.
  const keyRef = useRef<{ decisionId: string; key: string } | null>(null)

  const allowed = decision.manual.allowed
  const prUrl = safeHttpUrl(decision.pullRequest?.url)

  function submit() {
    if (keyRef.current?.decisionId !== decision.id) {
      keyRef.current = { decisionId: decision.id, key: newIdempotencyKey() }
    }
    merge.mutate({ decisionId: decision.id, idempotencyKey: keyRef.current.key })
  }

  return (
    <section aria-labelledby="merge-heading" className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 id="merge-heading" className="text-sm font-semibold">
          Мерж
        </h3>
        {decision.pullRequest && (
          <span className="text-sm">
            {prUrl ? (
              <a
                href={prUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 text-primary underline-offset-4 hover:underline"
              >
                Pull request #{decision.pullRequest.number}
                <ExternalLink className="size-3.5" aria-hidden />
              </a>
            ) : (
              <>Pull request #{decision.pullRequest.number}</>
            )}
          </span>
        )}
      </div>

      <div className="flex items-start gap-2 text-sm">
        {allowed ? (
          <ShieldCheck className="mt-0.5 size-4 shrink-0 text-success" aria-hidden />
        ) : (
          <ShieldAlert className="mt-0.5 size-4 shrink-0 text-warning" aria-hidden />
        )}
        <span>{allowed ? 'Сервер разрешил ручной мерж.' : 'Сервер не разрешает мерж.'}</span>
      </div>
      {!allowed && <Reasons reasons={decision.manual.reasons} />}

      {merge.isError && <ErrorNotice error={merge.error} />}
      {merge.isSuccess && (
        <p role="status" className="text-sm">
          Запрос на мерж принят. Итог появится в статусе «Мерж».
        </p>
      )}

      <div>
        <Button
          type="button"
          disabled={!allowed || merge.isPending}
          onClick={() => setConfirmOpen(true)}
        >
          <GitMerge aria-hidden />
          Выполнить мерж
        </Button>
      </div>

      <div className="flex flex-col gap-1 border-t pt-3 text-sm text-muted-foreground">
        <p>
          Автомерж:{' '}
          {decision.auto.enabled ? 'включён' : decision.auto.available ? 'выключен' : 'недоступен'}.
          Он включается явно, после предупреждения, и проходит те же проверки.
        </p>
        {!decision.auto.available && <Reasons reasons={decision.auto.reasons} />}
      </div>

      <AlertDialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Выполнить мерж?</AlertDialogTitle>
            <AlertDialogDescription>
              Изменения будут влиты в целевую ветку. Действие нельзя отменить из этого интерфейса.
              Сервер ещё раз проверит актуальность результата и права, и откажет, если что-то
              изменилось.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Отмена</AlertDialogCancel>
            <AlertDialogAction onClick={submit}>Выполнить мерж</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </section>
  )
}
