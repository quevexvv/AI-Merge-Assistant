from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select

from app.db.models import Attempt, CoverageReport, Installation, Repository, Run, WebhookDelivery
from app.db.session import SessionLocal
from app.worker.celery_app import celery_app


def _utcnow() -> datetime:
    return datetime.now(UTC)


@celery_app.task(name="runs.execute_pipeline", max_retries=0)
def execute_run_pipeline_task(run_id: str, simulate_offline_runner: bool = False) -> dict[str, str]:
    """
    Фоновое выполнение пайплайна прогона с сохранением состояния в БД.
    Покрытие и вердикт появляются ТОЛЬКО после завершения runner (или при явной симуляции).
    """
    run_uuid = UUID(run_id)
    with SessionLocal() as db:
        run = db.get(Run, run_uuid)
        if run is None:
            return {"run_id": run_id, "status": "not_found"}

        attempt = db.scalar(select(Attempt).where(Attempt.run_id == run_uuid))
        if not simulate_offline_runner:
            # Переводим в preparing, ожидая выполнения реального sandbox runner
            run.execution_state = "preparing"
            if attempt:
                attempt.attempt_state = "running"
            run.updated_at = _utcnow()
            db.commit()
            return {"run_id": run_id, "status": "preparing"}

        # Явный offline-режим для демонстрации / тестов
        run.execution_state = "completed"
        run.verdict = "passed"
        run.completed_at = _utcnow()
        run.updated_at = _utcnow()
        if attempt:
            attempt.attempt_state = "completed"
            attempt.completed_at = _utcnow()
            existing_cov = db.scalar(
                select(CoverageReport).where(CoverageReport.attempt_id == attempt.id)
            )
            if existing_cov is None:
                cov = CoverageReport(
                    attempt_id=attempt.id,
                    run_id=run.id,
                    covered_changed_lines=19,
                    executable_changed_lines=20,
                    coverage_percentage=95.0,
                    covered_files=json.dumps(["src/service.py"]),
                    uncovered_changed_lines=json.dumps([20]),
                    coverage_verdict="passed",
                )
                db.add(cov)
        db.commit()
        return {"run_id": run_id, "status": "completed_offline"}


@celery_app.task(name="uploads.parse_large_document", max_retries=0)
def parse_large_document_task(upload_id: str) -> dict[str, str]:
    _ = UUID(upload_id)
    return {"upload_id": upload_id, "status": "parsed"}


@celery_app.task(name="webhooks.process_delivery", max_retries=0)
def process_webhook_delivery_task(
    delivery_id: str,
    event_type: str,
    payload: dict[str, Any],
) -> dict[str, str]:
    """
    Обрабатывает проверенный webhook в БД:
    - при push / pull_request помечает затронутые прогоны как stale (и merge_status=conflict)
    - при удалении installation удаляет запись установки
    - выставляет WebhookDelivery.processed = True ТОЛЬКО после успешной обработки
    """
    with SessionLocal() as db:
        delivery = db.scalar(
            select(WebhookDelivery).where(WebhookDelivery.delivery_id == delivery_id)
        )
        if delivery is None:
            return {"delivery_id": delivery_id, "status": "missing"}
        if delivery.processed:
            return {"delivery_id": delivery_id, "status": "already_processed"}

        if event_type in ("push", "pull_request"):
            repo_data = payload.get("repository") or {}
            full_name = repo_data.get("full_name")
            runs_stmt = select(Run).where(
                Run.freshness == "current",
                Run.repo_id.in_(select(Repository.id).where(Repository.full_name == full_name)),
            )
            for run in db.scalars(runs_stmt).all():
                # Branch refs are not persisted yet; a repository event may affect any run.
                run.freshness = "stale"
                if run.merge_status in ("pending", "allowed"):
                    run.merge_status = "conflict"
                run.updated_at = _utcnow()

        elif event_type in ("installation", "installation_repositories"):
            action = payload.get("action")
            inst_id = str((payload.get("installation") or {}).get("id") or "")
            if action in ("deleted", "suspend", "removed") and inst_id:
                inst = db.scalar(
                    select(Installation).where(Installation.github_app_installation_id == inst_id)
                )
                if inst is not None:
                    db.delete(inst)

        delivery.processed = True
        delivery.processed_at = _utcnow()
        db.commit()
        return {"delivery_id": delivery_id, "event_type": event_type, "status": "processed"}
