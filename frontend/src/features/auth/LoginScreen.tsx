import { GitBranch } from 'lucide-react'
import { useLocation } from 'react-router'

import { githubLoginUrl } from '@/api/client'
import { Button } from '@/components/ui/button'

export function LoginScreen() {
  const location = useLocation()
  const returnTo = `${location.pathname}${location.search}`
  return (
    <div className="mx-auto flex max-w-md flex-col gap-5 py-16">
      <h1 className="text-2xl font-semibold tracking-tight">Войдите, чтобы запускать прогоны</h1>
      <p className="text-sm leading-relaxed text-muted-foreground">
        Сервис проверяет изменения между ветками публичного репозитория на GitHub: пишет сценарии и
        тесты, запускает их и показывает покрытие. Результаты и прогоны видны только вам.
      </p>
      <div>
        <Button asChild size="lg">
          <a href={githubLoginUrl(returnTo)}>
            <GitBranch aria-hidden />
            Войти через GitHub
          </a>
        </Button>
      </div>
    </div>
  )
}
