import { render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { CodeBlock } from './CodeBlock'

describe('CodeBlock', () => {
  it('выводит код как текст, а не как разметку', () => {
    const code = '<script>window.__pwned = true</script>\n<img src=x onerror="window.__pwned=true">'
    const { container } = render(<CodeBlock code={code} label="лог" />)
    expect(container.querySelector('script')).toBeNull()
    expect(container.querySelector('img')).toBeNull()
    expect(screen.getByLabelText('лог').querySelectorAll('pre')[1]?.textContent).toBe(code)
    expect((window as unknown as { __pwned?: boolean }).__pwned).toBeUndefined()
  })

  it('делает видимыми управляющие символы', () => {
    render(<CodeBlock code={'\u001b[31mFAIL'} label="лог" />)
    expect(screen.getByLabelText('лог').querySelectorAll('pre')[1]?.textContent).toBe('␛[31mFAIL')
  })

  it('после подсветки текст не меняется и разметка из кода не появляется', async () => {
    const code = 'def f():\n    return "<b>x</b>"\n'
    const { container } = render(<CodeBlock code={code} lang="python" label="py" />)
    await waitFor(() => expect(container.querySelector('.shiki-token')).not.toBeNull(), {
      timeout: 5000,
    })
    expect(container.querySelector('b')).toBeNull()
    const region = screen.getByLabelText('py')
    // Текст кода (вторая колонка) не изменился; первая колонка — номера строк.
    expect(region.querySelectorAll('pre')[1]?.textContent).toBe('def f():\n    return "<b>x</b>"')
    expect(region.querySelectorAll('pre')[0]?.textContent).toBe('1\n2')
  })
})
