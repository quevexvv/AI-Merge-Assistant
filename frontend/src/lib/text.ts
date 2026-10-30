/**
 * Подготовка недоверенного текста (логи, код, сообщения тестов) к показу.
 *
 * Текст всегда выводится как React-текст, никогда как HTML. Дополнительно делаем видимыми
 * управляющие и невидимые символы, которыми можно подменить смысл строки на экране
 * (ANSI-последовательности, BiDi-переопределения «Trojan Source», нулевая ширина).
 */

// Управляющие символы C0 (кроме \t и \n), DEL и C1.
// eslint-disable-next-line no-control-regex
const CONTROL = /[\u0000-\u0008\u000B-\u001F\u007F-\u009F]/g
// BiDi-управление и невидимые форматирующие символы.
const INVISIBLE = /[\u061C\u200B-\u200F\u202A-\u202E\u2060-\u2064\u2066-\u2069\uFEFF]/g

function hex(code: number): string {
  return code.toString(16).toUpperCase().padStart(4, '0')
}

export function toDisplayText(input: string): string {
  return input
    .replace(/\r\n?/g, '\n')
    .replace(CONTROL, (ch) => {
      const code = ch.charCodeAt(0)
      // C0 → символы блока «Control Pictures» (␛, ␀ …), остальные → <U+XXXX>.
      if (code <= 0x1f) return String.fromCharCode(0x2400 + code)
      if (code === 0x7f) return '␡'
      return `<U+${hex(code)}>`
    })
    .replace(INVISIBLE, (ch) => `<U+${hex(ch.charCodeAt(0))}>`)
}

/** Ссылка из серверных данных допускается только как http(s); иначе показываем обычным текстом. */
export function safeHttpUrl(value: string | null | undefined): string | null {
  if (!value) return null
  try {
    const url = new URL(value)
    return url.protocol === 'https:' || url.protocol === 'http:' ? url.toString() : null
  } catch {
    return null
  }
}
