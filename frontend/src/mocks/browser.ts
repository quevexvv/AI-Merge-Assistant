import { setupWorker } from 'msw/browser'

import { enableMockPersistence } from './db'
import { handlers } from './handlers'

// В браузере прогоны переживают перезагрузку страницы (в тестах — нет).
enableMockPersistence()

export const worker = setupWorker(...handlers)
