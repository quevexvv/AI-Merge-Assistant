# Frontend AI-Merge-Assistant

Веб-интерфейс сервиса (зона Артёма Х, см. [договор команды](../docs/team-guide.md)). Сейчас реализована вертикаль
**создание прогона → прогресс → результаты и покрытие**. Интерфейс русский, есть светлая и тёмная темы
(переключатель: светлая / как в системе / тёмная), основной сценарий — десктоп, на мобильных ничего не ломается.

## Запуск

Нужен Node.js 24 LTS (см. `.nvmrc`; проверено также на 22.x).

```bash
cd frontend
npm ci
npm run dev:mock     # интерфейс на моках MSW, backend не нужен
npm run dev          # против backend; прокси /api → http://localhost:8000 (VITE_API_PROXY_TARGET)
npm run check        # typecheck + eslint + prettier + тесты + сборка
```

| Команда                                       | Что делает                                                                |
| --------------------------------------------- | ------------------------------------------------------------------------- |
| `npm run typecheck` / `lint` / `format:check` | `tsc -b`, ESLint (с типами), Prettier                                     |
| `npm test` / `test:watch`                     | Vitest + Testing Library + MSW                                            |
| `npm run build`                               | `tsc -b && vite build`, моки в бандл не попадают                          |
| `npm run api:gen`                             | типы из `openapi/openapi.yaml` → `src/api/schema.d.ts`                    |
| `npm run api:gen:backend`                     | то же из `http://localhost:8000/openapi.json` (когда backend будет готов) |

Режим `dev:mock` — это `vite --mode mock` (без `.env`-файлов: корневой `.gitignore` исключает `.env.*`, а команда
работает и на Windows). Мок-«сервер» хранит созданные прогоны в `localStorage` (ключ `ama-mock-db`), поэтому после
обновления страницы они на месте; сбросить — удалить этот ключ.

Демо-репозитории в моках (каждый показывает своё состояние):

| Репозиторий           | Что показывает                                                 |
| --------------------- | -------------------------------------------------------------- |
| `demo/calc-service`   | успех: 92,8 %, порог достигнут, мерж разрешён сервером         |
| `demo/low-coverage`   | тесты прошли, покрытие ниже порога, мерж запрещён, три попытки |
| `demo/docs-only`      | нет измеряемых строк: N/A, вердикт «Не применим»               |
| `demo/failing-tests`  | упавший тест (найден дефект), лог с ANSI-символами             |
| `demo/env-error`      | ошибка окружения: покрытие «—», частичные результаты           |
| `demo/stale-result`   | 92,8 % и «Пройден», но результат устарел — мерж запрещён       |
| `demo/read-only`      | нет права записи, GitHub App не установлен                     |
| `demo/quota-exceeded` | создание прогона отклоняется с 429                             |

## Правила интерфейса и где они проверяются

| Правило                                                   | Реализация                                                                                                                                                          | Тест                                                                                                |
| --------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| Кнопка мержа зависит только от серверного решения         | `MergePanel`: `disabled = !decision.manual.allowed` (+ блокировка на время запроса)                                                                                 | `app.test.tsx`: «100 % но сервер запретил», «ниже порога но сервер разрешил»; `MergePanel.test.tsx` |
| Процент на клиенте не считается                           | `coverage-view.ts` берёт `percent` сервера; `formatPercent` лишь форматирует (усечение до десятых, не округление вверх: 89,96 ≠ 90)                                 | `format.test.ts`, `CoverageGate.test.tsx`                                                           |
| N/A не показывается как 100 %                             | при `not_applicable` и при нуле строк (даже если сервер прислал 100) — «N/A»                                                                                        | `CoverageGate.test.tsx`, `app.test.tsx`                                                             |
| Состояние, актуальность, вердикт, статус мержа — отдельно | `StatusStrip`: четыре независимых значка с подписями                                                                                                                | `status.test.tsx`, `app.test.tsx` (stale + «Пройден»)                                               |
| Логи и код — только текст                                 | `CodeBlock` собирает React-текст из токенов Shiki; `dangerouslySetInnerHTML`/`innerHTML` запрещены ESLint; `toDisplayText` делает видимыми ANSI, NUL и BiDi-символы | `CodeBlock.test.tsx`, `text.test.ts`                                                                |
| Дисклеймер о покрытии дословно                            | `src/constants/disclaimer.ts`                                                                                                                                       | `disclaimer.test.ts` сверяет строку с `docs/team-guide.md`                                          |
| Отсутствующий отчёт ≠ успех                               | `unavailable` → «—», файл `missing_report` → красное предупреждение, не 0 % и не N/A                                                                                | `CoverageGate.test.tsx`, `app.test.tsx`                                                             |
| Повтор действия не создаёт дубликат                       | `Idempotency-Key` на создание прогона и мерж; ключ меняется только при правке формы / смене решения                                                                 | `MergePanel.test.tsx`                                                                               |

## Контракт с backend — это ДОПУЩЕНИЕ (ASSUMPTION T1)

В репозитории пока нет OpenAPI backend, поэтому `openapi/openapi.yaml` — черновик ожиданий frontend; на нём
построены типы и моки. Источник истины — OpenAPI Артёма З. Когда он появится: `npm run api:gen:backend`, затем поправить
расхождения (TypeScript покажет их при `typecheck`). Что стоит согласовать в первую очередь:

1. Маршруты и поля (`/api/v1/session|repositories|runs…`), форма ошибки `{ error: { code, message, fields?, retryAfterSeconds? } }`.
2. `X-CSRF-Token` берётся из `Session.csrfToken` и добавляется ко всем небезопасным запросам; `Idempotency-Key` обязателен
   на `POST /runs` и `POST /runs/{id}/merge`.
3. `Run.verdict` — итог проверки по правилам допуска (`passed | failed | not_applicable | null`), отдельно от `state`,
   `freshness` и `mergeStatus`; набор значений `mergeStatus` (`not_requested | pending | merged | conflict | failed | unknown`).
4. `RunResults.coverage`: сервер присылает `percent`, счётчики, `thresholdPercent` и `meetsThreshold`; для файла возможен
   статус `missing_report`. `RunResults.mergeDecision` (`manual.allowed`, `reasons[]`, `auto`, `pullRequest`).
5. Интервалы опроса (`features/runs/polling.ts`): 2 с во время выполнения, 3–5 с пока мерж не завершён, 30 с у
   завершённого актуального прогона (чтобы заметить `stale`) — начальные значения.
6. `GET /auth/github/login?returnTo=` — редирект на вход GitHub (клиент только ссылается на него).

## Структура

```
openapi/openapi.yaml      черновик контракта
src/api/                  openapi-fetch клиент, типы, ошибки, TanStack Query-хуки
src/features/runs/        форма, статусы, прогресс, покрытие, сценарии, тесты, логи, панель мержа
src/features/auth/        экран входа и AuthGate (401 → вход)
src/components/           CodeBlock (Shiki), тема, ui/ — компоненты в структуре shadcn/ui
src/mocks/                MSW: handlers, in-memory «сервер», фикстуры (используются и в dev:mock, и в тестах)
src/pages/, src/app/      страницы, маршруты, провайдеры
```

## Отклонения от исходного плана и ограничения

- **TypeScript 6.0.3**, а не последний 7.x: так записано в договоре (раздел 2), и typescript-eslint пока поддерживает `<6.1`.
  Из-за этого у `openapi-typescript@7.13.0` (peer `typescript ^5`) добавлен `overrides` в `package.json`; генерация проверена.
- **shadcn/ui написан вручную.** Реестр `ui.shadcn.com` из среды сборки недоступен, поэтому исходники компонентов
  (`src/components/ui/`) написаны в структуре shadcn поверх `radix-ui`, `class-variance-authority`, `tailwind-merge`;
  есть `components.json`, так что `npx shadcn add/diff` будут работать. Mantine не понадобился. Выбор репозитория и веток —
  нативный `<select>` (удобнее на мобильных).
- **Версии зафиксированы точно** (`.npmrc`: `save-exact`), `package-lock.json` в репозитории. Это новые мажоры
  (react-router 8, msw 3, vitest 5, shiki 4, eslint 10): API сверен по установленным пакетам (например, в MSW 3 опция
  называется `onUnhandledFrame`).
- `public/mockServiceWorker.js` лежит в `public/` и попадает в `dist/`, но без запуска `dev:mock` не регистрируется.
- Файлы frontend с LF (`.gitattributes`, `.editorconfig`, Prettier `endOfLine: lf`). Файлы остальных частей репозитория не менялись.

## Ещё не сделано (следующие шаги)

- Установка GitHub App и выбор установки отдельно от входа; настоящий список репозиториев/веток с серверной пагинацией.
- Публикация тестов/PR, явное включение автомержа с предупреждением (сейчас автомерж только показывается, переключателя нет).
- Загрузка требований и кода без GitHub, экспорт `.feature`/`.py`/ZIP, дашборд, подсветка непокрытых строк в исходниках.
- Отмена прогона, уведомление о квоте до отправки формы (сейчас квота показана, но не блокирует: решает сервер).
