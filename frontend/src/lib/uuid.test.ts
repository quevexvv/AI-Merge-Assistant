import { afterEach, describe, expect, it, vi } from 'vitest'

import { newIdempotencyKey } from '@/api/client'

import { uuidV4 } from './uuid'

const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('uuidV4', () => {
  it('использует crypto.randomUUID, когда он есть', () => {
    vi.stubGlobal('crypto', { randomUUID: () => '11111111-1111-4111-8111-111111111111' })
    expect(uuidV4()).toBe('11111111-1111-4111-8111-111111111111')
  })

  it('без randomUUID (http, старый браузер) собирает UUID v4 из getRandomValues', () => {
    vi.stubGlobal('crypto', {
      getRandomValues: (a: Uint8Array) => {
        a.fill(0xff)
        return a
      },
    })
    const id = uuidV4()
    expect(id).toMatch(UUID_V4)
    expect(id).toBe('ffffffff-ffff-4fff-bfff-ffffffffffff')
  })

  it('без Web Crypto вообще всё равно возвращает корректный и разный ключ', () => {
    vi.stubGlobal('crypto', undefined)
    const a = uuidV4()
    const b = uuidV4()
    expect(a).toMatch(UUID_V4)
    expect(b).toMatch(UUID_V4)
    expect(a).not.toBe(b)
  })

  it('newIdempotencyKey не падает без crypto.randomUUID', () => {
    vi.stubGlobal('crypto', { getRandomValues: globalThis.crypto.getRandomValues.bind(crypto) })
    expect(newIdempotencyKey()).toMatch(UUID_V4)
  })
})
