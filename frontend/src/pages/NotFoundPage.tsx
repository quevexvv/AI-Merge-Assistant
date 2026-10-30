import { Link } from 'react-router'

import { Button } from '@/components/ui/button'

export function NotFoundPage() {
  return (
    <div className="flex max-w-md flex-col gap-3 py-12">
      <h1 className="text-xl font-semibold">Страница не найдена</h1>
      <p className="text-sm text-muted-foreground">
        Проверьте адрес или вернитесь к списку прогонов.
      </p>
      <div>
        <Button asChild variant="outline">
          <Link to="/runs">К прогонам</Link>
        </Button>
      </div>
    </div>
  )
}
