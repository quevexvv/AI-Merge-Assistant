# AI-Merge-Assistant — Backend (этап I0, схема T1)

## 1. Обзор этапа I0 (Схема T1)

Реализованные модули:

- `CoverageCalculator` (`app/core/coverage/calculator.py`) — расчёт покрытия изменённых исполняемых строк Python для GitHub-режима и режима загрузки.
- `RunOrchestrator` (`app/core/orchestrator/`) — конечный автомат управления прогоном с состояниями `queued` → `preparing` → `generating` → `validating` → `running` → `checking_coverage` → `improving` → `completed` / `failed` / `cancelled`.
- `MergeGate` (`app/core/merge/gate.py`) — серверный шлюз принятия решения о мерже с проверкой вердикта прогона (`passed`), порога покрытия (`100 * covered >= 90 * executable`), актуальности снимка (`current`), прав доступа и условий автомержа.
- `GitHubIdempotencyManager` (`app/core/github_idempotency.py`) — управление идемпотентными операциями с GitHub (push_tests в `ai-tests/<run-id>`, create_pr, merge_pr с передачей ожидаемого `head SHA`). Запись в исходную ветку и force-push запрещены. Защита от дублей и обработка статуса `unknown` (правило 10) через сверку фактического состояния в GitHub.
- 12 таблиц БД SQLAlchemy 2.0 (`app/db/models.py`): `users`, `sessions`, `github_connections`, `installations`, `repositories`, `runs`, `attempts`, `scenarios`, `artifacts`, `coverage_reports`, `github_actions`, `webhook_deliveries`.
- REST API роутеры и порты интеграции (`app/api/router.py`): `GitHubOAuthPort`, `GitHubIntegrationPort`, `TaskDispatcherPort`, `SqlAlchemyGitHubActionRepository`.
- Фоновые задачи Celery (`app/worker/tasks.py`): `execute_run_pipeline_task`, `parse_large_document_task`, `process_webhook_delivery_task`.

Архитектурные границы I0:

- Внешние вызовы (GitHub OAuth, GitHub API, Celery Dispatcher) инкапсулированы в подменяемые порты (`GitHubOAuthPort`, `GitHubIntegrationPort`, `TaskDispatcherPort`).
- Демо-режим по умолчанию выключен (`OFFLINE_DEMO_MODE=false`).

## 2. Быстрый старт и проверка из чистого окружения

Создание виртуального окружения с нуля:

```bash
cd backend
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
```

Запуск тестов и линтера:

```bash
.venv/bin/python -m ruff check app/ tests/
.venv/bin/python -m pytest -v
```

Запуск локального dev-сервера FastAPI:

```bash
.venv/bin/uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## 3. Переменные окружения (`.env`)

Основные настройки из `app/core/config.py`:

| Переменная | Описание | Дефолт для локальной разработки / тестов |
|---|---|---|
| `DATABASE_URL` | URL подключения к PostgreSQL | `postgresql://postgres:postgres@localhost:5432/ai_merge` |
| `REDIS_URL` | URL подключения к Redis (Celery broker) | `redis://localhost:6379/0` |
| `OFFLINE_DEMO_MODE` | Режим демонстрации без реального GitHub OAuth | `false` |
| `TOKEN_ENCRYPTION_KEY` | Ключ шифрования токенов GitHub | `dev-encryption-key-change-in-production` |
| `SESSION_SECRET_KEY` | Секретный ключ сессий | `dev-session-secret-change-in-production` |
| `GITHUB_WEBHOOK_SECRET` | Секрет для проверки подписи webhook GitHub | `dev-webhook-secret-change-in-production` |
| `ARTIFACTS_VOLUME_PATH` | Путь к хранилищу артефактов | `/tmp/ai_merge_artifacts` |
