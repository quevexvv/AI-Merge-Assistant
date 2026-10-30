import { Info } from 'lucide-react'

import type { Scenario } from '@/api/types'
import { CodeBlock } from '@/components/CodeBlock'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { toDisplayText } from '@/lib/text'

import { labelOf, ORIGIN_LABEL } from './labels'

export function ScenariosPanel({ scenarios }: { scenarios: Scenario[] }) {
  if (scenarios.length === 0) {
    return <p className="text-sm text-muted-foreground">Сценариев нет.</p>
  }
  const hasInferred = scenarios.some((s) => s.origin === 'inferred')
  return (
    <div className="flex flex-col gap-5">
      {hasInferred && (
        <Alert tone="info">
          <Info aria-hidden />
          <AlertDescription>
            <p>
              Сценарии с пометкой «Выведено из diff» построены по изменениям кода, а не по вашему
              описанию. Проверьте, что описанное в них поведение действительно ожидаемо.
            </p>
          </AlertDescription>
        </Alert>
      )}
      <ol className="flex flex-col gap-6">
        {scenarios.map((scenario) => (
          <li key={scenario.id} className="flex min-w-0 flex-col gap-2">
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="text-sm font-semibold">{toDisplayText(scenario.title)}</h3>
              <Badge tone={scenario.origin === 'inferred' ? 'warning' : 'info'}>
                {labelOf(ORIGIN_LABEL, scenario.origin)}
              </Badge>
            </div>
            {scenario.sourceRefs.length > 0 && (
              <p className="text-xs text-muted-foreground">
                Источники: {scenario.sourceRefs.map((r) => toDisplayText(r)).join(', ')}
              </p>
            )}
            <CodeBlock
              code={scenario.gherkin}
              lang="gherkin"
              label={`Сценарий: ${scenario.title}`}
            />
          </li>
        ))}
      </ol>
    </div>
  )
}
