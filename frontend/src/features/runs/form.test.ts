import { describe, expect, it } from 'vitest'

import { validateRunForm, type RunFormValues } from './form'

const base: RunFormValues = {
  repositoryId: 'r1',
  sourceBranch: 'feature/x',
  targetBranch: 'main',
  mode: 'default',
  description: 'Что изменилось',
}

describe('validateRunForm', () => {
  it('принимает корректную форму', () => {
    expect(validateRunForm(base)).toEqual({})
  })

  it('в режиме default описание обязательно', () => {
    expect(validateRunForm({ ...base, description: '   ' }).description).toBeDefined()
  })

  it('в режиме lazy описание не требуется', () => {
    expect(validateRunForm({ ...base, mode: 'lazy', description: '' })).toEqual({})
  })

  it('требует репозиторий и обе ветки, ветки должны различаться', () => {
    const errors = validateRunForm({
      ...base,
      repositoryId: '',
      sourceBranch: '',
      targetBranch: '',
    })
    expect(Object.keys(errors).sort()).toEqual(['repositoryId', 'sourceBranch', 'targetBranch'])
    expect(validateRunForm({ ...base, sourceBranch: 'main' }).targetBranch).toBeDefined()
  })
})
