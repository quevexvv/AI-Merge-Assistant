/**
 * UUID v4 без обязательной зависимости от `crypto.randomUUID`.
 *
 * `randomUUID` есть только в защищённом контексте (https или localhost) и в современных браузерах:
 * при открытии по `http://<ip>:5173` или в старом браузере его нет, а падение при отрисовке формы
 * роняет всю страницу. `getRandomValues` доступен везде, в том числе по обычному http.
 */
export function uuidV4(): string {
  const c: Crypto | undefined = globalThis.crypto
  if (typeof c?.randomUUID === 'function') return c.randomUUID()

  const bytes = new Uint8Array(16)
  if (typeof c?.getRandomValues === 'function') {
    c.getRandomValues(bytes)
  } else {
    // Совсем без Web Crypto: ключ идемпотентности должен быть уникальным, а не секретным.
    for (let i = 0; i < bytes.length; i++) bytes[i] = Math.floor(Math.random() * 256)
  }
  // Версия 4 и вариант RFC 4122.
  bytes[6] = (bytes[6]! & 0x0f) | 0x40
  bytes[8] = (bytes[8]! & 0x3f) | 0x80

  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, '0'))
  return [
    hex.slice(0, 4).join(''),
    hex.slice(4, 6).join(''),
    hex.slice(6, 8).join(''),
    hex.slice(8, 10).join(''),
    hex.slice(10, 16).join(''),
  ].join('-')
}
