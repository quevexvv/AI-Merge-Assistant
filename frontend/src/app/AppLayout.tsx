import { GitPullRequestArrow, Plus } from 'lucide-react'
import { NavLink, Outlet } from 'react-router'

import { useSession } from '@/api/hooks'
import { ThemeToggle } from '@/components/ThemeToggle'
import { Button } from '@/components/ui/button'
import { AuthGate } from '@/features/auth/AuthGate'
import { cn } from '@/lib/utils'

function NavItem({ to, end, children }: { to: string; end?: boolean; children: string }) {
  return (
    <NavLink
      to={to}
      end={end}
      className={({ isActive }) =>
        cn(
          'rounded-md px-3 py-1.5 text-sm font-medium transition-colors',
          isActive
            ? 'bg-accent text-accent-foreground'
            : 'text-muted-foreground hover:text-foreground',
        )
      }
    >
      {children}
    </NavLink>
  )
}

function UserBadge() {
  const { data } = useSession()
  if (!data) return null
  return (
    <span
      className="hidden max-w-40 truncate text-sm text-muted-foreground sm:inline"
      title={data.user.login}
    >
      {data.user.login}
    </span>
  )
}

export function AppLayout() {
  return (
    <div className="flex min-h-svh flex-col">
      <a
        href="#main"
        className="sr-only z-50 rounded-md bg-primary px-3 py-2 text-primary-foreground focus:not-sr-only focus:absolute focus:top-2 focus:left-2"
      >
        К основному содержимому
      </a>
      <header className="border-b bg-card">
        <div className="mx-auto flex w-full max-w-6xl flex-wrap items-center gap-x-4 gap-y-2 px-4 py-2.5 sm:px-6">
          <NavLink to="/runs" className="mr-2 flex items-center gap-2 font-semibold">
            <span className="inline-flex size-7 items-center justify-center rounded-md bg-primary text-primary-foreground">
              <GitPullRequestArrow className="size-4" aria-hidden />
            </span>
            AI-Merge-Assistant
          </NavLink>
          <nav aria-label="Основная навигация" className="flex items-center gap-1">
            <NavItem to="/runs" end>
              Прогоны
            </NavItem>
          </nav>
          <div className="ml-auto flex items-center gap-3">
            <UserBadge />
            <Button asChild size="sm">
              <NavLink to="/runs/new">
                <Plus aria-hidden />
                Новый прогон
              </NavLink>
            </Button>
            <ThemeToggle />
          </div>
        </div>
      </header>
      <main id="main" className="mx-auto w-full max-w-6xl min-w-0 flex-1 px-4 py-6 sm:px-6">
        <AuthGate>
          <Outlet />
        </AuthGate>
      </main>
    </div>
  )
}
