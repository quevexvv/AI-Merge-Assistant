import { CreateRunForm } from '@/features/runs/CreateRunForm'

export function NewRunPage() {
  return (
    <div className="flex flex-col gap-6">
      <title>Новый прогон · AI-Merge-Assistant</title>
      <header className="flex max-w-2xl flex-col gap-1">
        <h1 className="text-2xl font-semibold tracking-tight">Новый прогон</h1>
        <p className="text-sm text-muted-foreground">
          Выберите репозиторий и две ветки. Сервис зафиксирует их коммиты, напишет тесты на
          изменения, запустит их и покажет покрытие.
        </p>
      </header>
      <CreateRunForm />
    </div>
  )
}
