# GitHub-интеграция: проект сервисного контракта

Статус на 2026-10-30: предложение для согласования с владельцем E6, а не реализованный
клиент. Источник требований — [договор команды](team-guide.md). В `dev` GitHub-модуль
ещё пуст. Контракты ниже позволяют начать его разработку с mock HTTP независимо от API/БД.
Проверка через реальный GitHub App и sandbox не проводилась.

## Ответственность и границы

| Владелец | Ответственность |
|---|---|
| Вадим, `services/github/` | Регистрация/permissions App, протокол OAuth/App, HTTP-клиент, права, снимки, публикация, checks, merge, read-only reconciliation, HMAC webhook |
| Артём З, E6 | HTTP/cookie/CSRF/state, доверенный actor, БД токенов/операций, очередь, серверный MergeDecision |
| Иван и runner, E3/E4 | Допустимость TestBundle, финальный прогон, измеренный отчёт точного кандидата |
| Макс, размещение | Внешние секреты, публичные callback/webhook URL; для E7 отдельно требуются демо-репозиторий, установка App и тестовый пользователь |

LLM не импортирует GitHub-клиент. Клиент не реализует сессии, миграции, очередь или
собственный расчёт порога покрытия. Browser передаёт намерение; actor и допуск создаёт сервер.

## Предлагаемые входы E6

**ASSUMPTION G1:** имена полей/методов ниже предварительные; версия примеров `proposal-1`
не заменяет ни общий T1, ни LLM `schema_version="1"`. Исполняемые DTO появятся в каркасе.

- `ActorContext`: внутренний user ID и подтверждённый GitHub user ID; login — отображение,
  а не доказательство прав. Repository ID и installation ID сверяются по GitHub заново.
- `RepositorySelection`: repository ID, owner/name, installation ID, source/target refs.
  В первом релизе допускаются только публичные репозитории согласованного Python-профиля.
- `SnapshotRequest`: run ID, selection, ожидаемые полные source/target SHA.
  Результат фиксирует snapshot ID, исходные refs/SHA, candidate head/tree SHA, полный diff
  и content hash. Усечённый diff и конфликт дают ошибку, а не частичный успешный снимок.
- `PublicationIntent`: actor, run/attempt/snapshot ID, selection, проверенный шлюзом bundle
  с хешем и точным разрешённым набором путей, ожидаемые source/target/parent SHA.
  Содержимое прикладного кода, существующих тестов и конфигурации не изменяется.
- `MergeIntent`: actor, repository/PR/head/base, точный окончательный кандидат,
  серверный MergeDecision с run/attempt/snapshot/bundle hash и явным manual/auto намерением.
  После синхронизации target нужен новый снимок и новый реальный отчёт для него.

## Порты хранения и идемпотентности

**ASSUMPTION G2:** E6 предоставляет следующие операции; SQL/реализация остаются у владельца БД.

| Порт | Минимальная семантика |
|---|---|
| `TokenStore.load_user / compare_and_swap_user` | Шифрованное хранение, expiry, атомарная ротация access/refresh по версии; отказ CAS не запускает повторный обмен старого refresh token |
| `OperationStore.claim` | Атомарное создание уникального ключа намерения до HTTP-записи; вернуть acquired/existing вместе с сохранённым контекстом |
| `OperationStore.get / complete` | Восстановить контекст после рестарта, записать результат по ожидаемой версии; запретить конкурентным обработчикам повтор записи |
| `DecisionProvider.get_current` | Доверенный актуальный допуск только для совпавших actor/run/attempt/snapshot/candidate/bundle |

Запись операции содержит key, operation ID/type, actor/repository/installation ID,
run/attempt/snapshot ID, source/target refs и SHA, candidate head/tree, bundle hash,
PR/check IDs при наличии, branch, состояние и версию. Ключ связан с полным намерением:
повтор ключа с иным содержимым — conflict. Словари процесса не заменяют это хранилище.
После unknown выполняется только чтение GitHub по сохранённым идентификаторам.
Отсутствие ответа или отсутствие объекта при одном чтении не разрешает повторную запись.

## Методы и результаты

**ASSUMPTION G3:** сервисные методы принимают отдельный ограниченный deadline/request budget.
Чтение может иметь конечные повторы; неоднозначная запись не повторяется автоматически.

| Метод | Результат и условия |
|---|---|
| `read_snapshot(actor, request)` | Полный immutable снимок либо forbidden/not_found/stale/conflict; credentials в результате нет |
| `publish_tests(actor, intent)` | Ветка `ai-tests/<run-id>`, согласованный коммит и PR; каждый внешний эффект имеет свою сохранённую операцию |
| `prepare_candidate(actor, request)` | Актуальный относительно target head/tree; новый снимок возвращается оркестратору для финального прогона |
| `publish_check(actor, result)` | Check нашего App на точном candidate SHA по доверенному результату; unknown/N/A/failed не становятся success |
| `merge(actor, intent)` | Проверка текущих прав/refs/PR/rules, затем запрос с expected head; success только после подтверждения GitHub |
| `reconcile(actor, operation_id)` | Read-only success/conflict/forbidden/unknown; совпадение названия ветки/PR само по себе не доказывает принадлежность операции |
| `verify_webhook(raw_body, headers)` | HMAC до JSON, нормализованное поддерживаемое событие с delivery ID; дедупликация и очередь — E6 |

При выключенном серверном auto согласии запрос auto отклоняется, без подмены на manual.
Неизвестные права/rules блокируют запись. Для auto обязательны строгая защита target,
актуальность кандидата и required check именно нашего App без обхода правил.
User token ограничивает действия пользователя; installation token не повышает его права.

## Примеры обмена без credentials

Сокращённые envelope-примеры; полные DTO/валидация — следующий этап, UUID/SHA здесь синтетические.
Снимок и публикация используют один run; мерж относится к новому окончательному snapshot.

```json
{"schema_version":"proposal-1","method":"read_snapshot","actor":{"user_id":"11111111-1111-4111-8111-111111111111","github_user_id":101},"request":{"run_id":"22222222-2222-4222-8222-222222222222","repository_id":202,"installation_id":303,"source_ref":"feature/example","target_ref":"dev","expected_source_sha":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","expected_target_sha":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}}
```

```json
{"schema_version":"proposal-1","method":"publish_tests","request":{"operation_key":"run-2222:attempt-1:publish","run_id":"22222222-2222-4222-8222-222222222222","snapshot_id":"initial-snapshot","repository_id":202,"branch":"ai-tests/22222222-2222-4222-8222-222222222222","expected_parent_sha":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","bundle_hash":"cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc","allowed_paths":["generated_tests/test_example.py"]},"result":{"status":"success","pr_number":12,"branch":"ai-tests/22222222-2222-4222-8222-222222222222","actual_sha":"dddddddddddddddddddddddddddddddddddddddd","requires_final_validation":true}}
```

```json
{"schema_version":"proposal-1","method":"merge","request":{"operation_key":"run-2222:attempt-2:merge","repository_id":202,"pr_number":12,"target_ref":"dev","expected_head_sha":"eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee","decision":{"run_id":"22222222-2222-4222-8222-222222222222","attempt_id":"33333333-3333-4333-8333-333333333333","snapshot_id":"final-snapshot","verified_candidate_sha":"eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee","allowed":true,"mode":"manual"}},"result":{"operation_id":"44444444-4444-4444-8444-444444444444","status":"unknown","actual_sha":null,"safe_reason":"Write outcome requires reconciliation"}}
```

При отказе в правах возвращается `status="forbidden"`, без вызова записи; при смене
source/target/head — `stale` до записи. `conflict` означает несовместимое подтверждённое
состояние; `unknown` — недоказанный исход отправленной записи. `not_found` относится к чтению;
`rate_limited`/ошибки конфигурации не маскируются под успех. Логи не содержат credentials,
промптов, исходников и полных ответов; безопасная причина не копирует тело ответа GitHub.

## Согласование с каркасом приложения

В PR #9 на 7628cee порты пока находятся в `api/router.py`; это не принятый GitHub-клиент.
Его `MergeDecision` содержит source/target/bundle, но не candidate SHA/snapshot ID;
`GitHubActionRecord` не хранит полный контекст G2. Эти расхождения нужно согласовать с E6
до подключения записей. Не импортировать неподключённые API/DB DTO в сервис ради совместимости.
Перед №31 требуется согласовать поля G1–G3, атомарный claim/ротацию и передачу финального
кандидата. Fake-порты явно внедряются в тестах; рабочий неподключённый порт отказывает.
Permissions/callback/webhook настройки уточняются при реализации соответствующих методов;
секреты остаются вне Git. Реальный read/write приёмочный прогон требует отдельного E7.
