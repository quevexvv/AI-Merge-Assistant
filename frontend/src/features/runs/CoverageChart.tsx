import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

import type { CoverageFile } from '@/api/types'

const MAX_ROWS = 12
const ROW_HEIGHT = 34

interface Row {
  path: string
  label: string
  covered: number
  missing: number
}

function toRows(files: CoverageFile[]): { rows: Row[]; hidden: number } {
  const measured = files.filter((f) => f.status === 'measured' && f.executable > 0)
  const sorted = [...measured].sort((a, b) => b.missing - a.missing || a.path.localeCompare(b.path))
  const rows = sorted.slice(0, MAX_ROWS).map((f) => ({
    path: f.path,
    label: f.path.split('/').slice(-2).join('/'),
    covered: f.covered,
    missing: f.missing,
  }))
  return { rows, hidden: measured.length - rows.length }
}

interface TooltipPayload {
  payload?: Row
}

function ChartTooltip({ active, payload }: { active?: boolean; payload?: TooltipPayload[] }) {
  const row = payload?.[0]?.payload
  if (!active || !row) return null
  return (
    <div className="rounded-md border bg-popover px-3 py-2 text-xs text-popover-foreground shadow-sm">
      <div className="mb-1 font-mono font-medium break-all">{row.path}</div>
      <div className="tabular">Покрыто строк: {row.covered}</div>
      <div className="tabular">Не покрыто строк: {row.missing}</div>
    </div>
  )
}

/** Строки по файлам: покрыто / не покрыто. Числа — счётчики сервера; та же информация есть в таблице. */
export function CoverageChart({ files }: { files: CoverageFile[] }) {
  const { rows, hidden } = toRows(files)
  if (rows.length === 0) return null
  const height = rows.length * ROW_HEIGHT + 36
  return (
    <figure className="flex flex-col gap-2">
      <figcaption className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
        <span className="font-medium">Строки по файлам</span>
        <span className="inline-flex items-center gap-1.5 text-muted-foreground">
          <span aria-hidden className="inline-block size-2.5 rounded-sm bg-viz-covered" />
          Покрыто
        </span>
        <span className="inline-flex items-center gap-1.5 text-muted-foreground">
          <span aria-hidden className="inline-block size-2.5 rounded-sm bg-viz-missing" />
          Не покрыто
        </span>
      </figcaption>
      <div aria-hidden className="w-full" style={{ height }}>
        <ResponsiveContainer width="100%" height={height} initialDimension={{ width: 640, height }}>
          <BarChart
            data={rows}
            layout="vertical"
            margin={{ top: 4, right: 12, bottom: 4, left: 0 }}
            barCategoryGap={10}
          >
            <CartesianGrid horizontal={false} stroke="var(--border)" />
            <XAxis
              type="number"
              allowDecimals={false}
              tickLine={false}
              axisLine={false}
              tick={{ fill: 'var(--muted-foreground)', fontSize: 12 }}
            />
            <YAxis
              type="category"
              dataKey="label"
              width={150}
              tickLine={false}
              axisLine={false}
              tick={{ fill: 'var(--foreground)', fontSize: 12 }}
            />
            <Tooltip content={<ChartTooltip />} cursor={{ fill: 'var(--muted)', opacity: 0.5 }} />
            <Bar
              dataKey="covered"
              stackId="lines"
              fill="var(--viz-covered)"
              stroke="var(--card)"
              strokeWidth={2}
              radius={[4, 0, 0, 4]}
              isAnimationActive={false}
            />
            <Bar
              dataKey="missing"
              stackId="lines"
              fill="var(--viz-missing)"
              stroke="var(--card)"
              strokeWidth={2}
              radius={[0, 4, 4, 0]}
              isAnimationActive={false}
            />
          </BarChart>
        </ResponsiveContainer>
      </div>
      <p className="text-xs text-muted-foreground">
        Те же числа есть в таблице ниже.
        {hidden > 0 &&
          ` На графике ${rows.length} файлов с наибольшим числом непокрытых строк, ещё ${hidden} — только в таблице.`}
      </p>
    </figure>
  )
}
