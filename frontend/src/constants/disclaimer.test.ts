import guide from '../../../docs/team-guide.md?raw'
import { describe, expect, it } from 'vitest'

import { COVERAGE_DISCLAIMER } from './disclaimer'

describe('надпись о покрытии', () => {
  it('совпадает дословно с текстом в docs/team-guide.md', () => {
    const lf = guide.replace(/\r\n/g, '\n')
    const quoted = lf.split('\n').find((line) => line.startsWith('> Покрытие показывает'))
    expect(quoted, 'в team-guide.md не найдена согласованная надпись').toBeDefined()
    expect(COVERAGE_DISCLAIMER).toBe(quoted!.slice(2))
  })
})
