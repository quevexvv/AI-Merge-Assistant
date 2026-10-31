import { Monitor, Moon, Sun } from 'lucide-react'

import { cn } from '@/lib/utils'

import { useTheme, type Theme } from './theme-context'

const OPTIONS: { value: Theme; label: string; icon: typeof Sun }[] = [
  { value: 'light', label: 'Светлая тема', icon: Sun },
  { value: 'system', label: 'Тема как в системе', icon: Monitor },
  { value: 'dark', label: 'Тёмная тема', icon: Moon },
]

export function ThemeToggle() {
  const { theme, setTheme } = useTheme()
  return (
    <div
      role="group"
      aria-label="Тема оформления"
      className="inline-flex rounded-md bg-secondary p-0.5"
    >
      {OPTIONS.map(({ value, label, icon: Icon }) => (
        <button
          key={value}
          type="button"
          aria-label={label}
          title={label}
          aria-pressed={theme === value}
          onClick={() => setTheme(value)}
          className={cn(
            'inline-flex size-7 items-center justify-center rounded-[5px] text-muted-foreground transition-colors',
            theme === value ? 'bg-card text-foreground shadow-sm' : 'hover:text-foreground',
          )}
        >
          <Icon className="size-4" aria-hidden />
        </button>
      ))}
    </div>
  )
}
