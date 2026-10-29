from __future__ import annotations

import hashlib
import hmac
import json
from urllib.parse import parse_qs, urlparse
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.api.router import (
    GitHubOAuthPort,
    OAuthUserIdentity,
    SqlAlchemyGitHubActionRepository,
    get_oauth_port,
    github_port,
    oauth_states_registry,
    task_dispatcher,
)
from app.core.config import settings
from app.core.security import decrypt_token, encrypt_token
from app.db import models  # noqa: F401
from app.db.base import Base
from app.db.models import (
    Artifact,
    Attempt,
    CoverageReport,
    GitHubAction,
    Installation,
    Repository,
    Run,
    Scenario,
    User,
    WebhookDelivery,
)
from app.db.models import (
    Session as DbSession,
)
from app.db.session import SessionLocal
from app.main import app
from app.schemas import GitHubOperationStatus


class FakeTestOAuthPort(GitHubOAuthPort):
    """Тестовый OAuth-порт, проверяющий code и возвращающий подтверждённую личность."""

    def exchange_code(self, code: str) -> OAuthUserIdentity:
        if code == "valid-code-user-1":
            return OAuthUserIdentity(
                github_id="10001",
                github_login="aruchimo",
                display_name="Aruchimo",
                access_token="ghu_token_user_1",
                installations=["inst-1"],
            )
        if code == "valid-code-user-2":
            return OAuthUserIdentity(
                github_id="10002",
                github_login="other-user",
                display_name="Other User",
                access_token="ghu_token_user_2",
                installations=["inst-2"],
            )
        return super().exchange_code(code)


@pytest.fixture(autouse=True)
def clean_environment():
    settings.offline_demo_mode = False
    oauth_states_registry.clear()
    github_port.repo_state.clear()
    github_port.merge_outcomes.clear()
    github_port.reconcile_outcomes.clear()
    github_port.merge_calls.clear()
    task_dispatcher.dispatched_runs.clear()
    task_dispatcher.dispatched_uploads.clear()
    task_dispatcher.dispatched_webhooks.clear()
    SqlAlchemyGitHubActionRepository._key_to_reconciliation.clear()
    SqlAlchemyGitHubActionRepository._contexts.clear()

    with SessionLocal() as db:
        for model in (
            WebhookDelivery,
            GitHubAction,
            CoverageReport,
            Artifact,
            Scenario,
            Attempt,
            Run,
            Repository,
            Installation,
            DbSession,
            User,
        ):
            db.execute(delete(model))
        db.commit()

    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def _login_via_oauth(client: TestClient, code: str = "valid-code-user-1") -> str:
    login_resp = client.get("/auth/github/login", follow_redirects=False)
    assert login_resp.status_code == 302
    state = parse_qs(urlparse(login_resp.headers["location"]).query)["state"][0]
    binding_cookie = login_resp.cookies.get("oauth_state_binding")
    assert binding_cookie is not None

    client.cookies.set("oauth_state_binding", binding_cookie)
    cb_resp = client.get(f"/auth/github/callback?state={state}&code={code}")
    assert cb_resp.status_code == 200
    session_token = cb_resp.cookies.get("session_token")
    assert session_token is not None
    return session_token


def test_sqlalchemy_metadata_has_all_12_tables():
    expected_tables = {
        "users",
        "sessions",
        "github_connections",
        "installations",
        "repositories",
        "runs",
        "attempts",
        "scenarios",
        "artifacts",
        "coverage_reports",
        "github_actions",
        "webhook_deliveries",
    }
    assert expected_tables.issubset(set(Base.metadata.tables.keys()))


def test_token_encryption_roundtrip():
    raw = "ghu_secret_github_user_token_12345"
    enc = encrypt_token(raw)
    assert raw not in enc
    assert decrypt_token(enc) == raw


def test_no_builtin_demo_tokens_and_oauth_validates_code_and_browser_state():
    client = TestClient(app)

    # 1. Публичные demo-session-token отвергаются рабочим приложением
    resp = client.get(
        "/runs/11111111-1111-1111-1111-111111111111",
        headers={"Authorization": "Bearer demo-session-token"},
    )
    assert resp.status_code == 401

    # 2. Без тестового порта произвольный code в callback отвергается (401)
    login_resp = client.get("/auth/github/login", follow_redirects=False)
    state = parse_qs(urlparse(login_resp.headers["location"]).query)["state"][0]
    binding = login_resp.cookies["oauth_state_binding"]
    client.cookies.set("oauth_state_binding", binding)
    bad_code_resp = client.get(f"/auth/github/callback?state={state}&code=arbitrary")
    assert bad_code_resp.status_code == 401

    # 3. Чужой браузер (без oauth_state_binding cookie) получает 403
    login_resp2 = client.get("/auth/github/login", follow_redirects=False)
    state2 = parse_qs(urlparse(login_resp2.headers["location"]).query)["state"][0]
    client.cookies.clear()
    no_cookie_resp = client.get(f"/auth/github/callback?state={state2}&code=valid-code-user-1")
    assert no_cookie_resp.status_code == 403


def test_create_run_persists_in_db_without_fake_coverage_and_validates_description():
    app.dependency_overrides[get_oauth_port] = FakeTestOAuthPort
    client = TestClient(app)
    token1 = _login_via_oauth(client, "valid-code-user-1")
    token2 = _login_via_oauth(client, "valid-code-user-2")
    headers1 = {"Authorization": f"Bearer {token1}"}
    headers2 = {"Authorization": f"Bearer {token2}"}

    # Регрессия P2: пустое или пробельное описание в режиме default отклоняется (400)
    blank_resp = client.post(
        "/runs",
        headers=headers1,
        json={
            "source_type": "github",
            "repo_owner": "aruchimo",
            "repo_name": "demo-python-repo",
            "source_sha": "1111111",
            "target_sha": "2222222",
            "mode": "default",
            "description": "   ",
        },
    )
    assert blank_resp.status_code == 400

    # В режиме lazy описание не требуется
    create_resp = client.post(
        "/runs",
        headers=headers1,
        json={
            "source_type": "github",
            "repo_owner": "aruchimo",
            "repo_name": "demo-python-repo",
            "source_sha": "1111111",
            "target_sha": "2222222",
            "mode": "lazy",
        },
    )
    assert create_resp.status_code == 202
    run_id = create_resp.json()["run_id"]
    assert UUID(run_id) in task_dispatcher.dispatched_runs

    # До завершения runner вердикт == None, а /coverage возвращает 404 (нет фейковых 95%)
    status_resp = client.get(f"/runs/{run_id}/status", headers=headers1)
    assert status_resp.status_code == 200
    assert status_resp.json()["verdict"] is None
    cov_resp = client.get(f"/runs/{run_id}/coverage", headers=headers1)
    assert cov_resp.status_code == 404

    # Чужой пользователь получает 403 без утечки run_id
    foreign_resp = client.get(f"/runs/{run_id}", headers=headers2)
    assert foreign_resp.status_code == 403
    assert "run_id" not in foreign_resp.json()
    assert run_id not in str(foreign_resp.json())


def test_merge_executes_via_github_port_and_reconciles_unknown_status():
    app.dependency_overrides[get_oauth_port] = FakeTestOAuthPort
    client = TestClient(app)
    token = _login_via_oauth(client, "valid-code-user-1")
    headers = {"Authorization": f"Bearer {token}"}

    run_id = client.post(
        "/runs",
        headers=headers,
        json={
            "source_type": "github",
            "repo_owner": "aruchimo",
            "repo_name": "demo-python-repo",
            "source_sha": "1111111",
            "target_sha": "2222222",
            "mode": "default",
            "description": "Implement feature X",
        },
    ).json()["run_id"]
    run_uuid = UUID(run_id)

    # Завершаем прогон в БД с валидным отчётом покрытия
    with SessionLocal() as db:
        run = db.get(Run, run_uuid)
        attempt = db.query(Attempt).filter(Attempt.run_id == run_uuid).first()
        run.execution_state = "completed"
        run.verdict = "passed"
        db.add(
            CoverageReport(
                attempt_id=attempt.id,
                run_id=run_uuid,
                covered_changed_lines=19,
                executable_changed_lines=20,
                coverage_percentage=95.0,
                covered_files=json.dumps(["src/service.py"]),
                uncovered_changed_lines=json.dumps([20]),
                coverage_verdict="passed",
            )
        )
        db.commit()

    # 1. Если первый вызов к GitHub вернул UNKNOWN (таймаут), статус остаётся pending (202)
    github_port.merge_outcomes[run_id] = GitHubOperationStatus.UNKNOWN
    first = client.post(f"/runs/{run_id}/merge", headers=headers, json={"mode": "manual"})
    assert first.status_code == 202
    assert first.json()["merge_status"] == "pending"
    assert len(github_port.merge_calls) == 1

    # 2. Повторный вызов выполняет сверку (reconcile_unknown) и подтверждает SUCCESS -> completed
    github_port.reconcile_outcomes["aruchimo/demo-python-repo"] = GitHubOperationStatus.SUCCESS
    second = client.post(f"/runs/{run_id}/merge", headers=headers, json={"mode": "manual"})
    assert second.status_code == 200
    assert second.json()["merge_status"] == "completed"
    assert second.json()["reconciled"] is True
    assert len(github_port.merge_calls) == 1  # Повторного вызова merge_pr не было!


def test_webhook_persists_in_db_updates_run_freshness_and_deduplicates():
    app.dependency_overrides[get_oauth_port] = FakeTestOAuthPort
    client = TestClient(app)
    token = _login_via_oauth(client, "valid-code-user-1")
    headers = {"Authorization": f"Bearer {token}"}

    run_id = client.post(
        "/runs",
        headers=headers,
        json={
            "source_type": "github",
            "repo_owner": "aruchimo",
            "repo_name": "demo-python-repo",
            "source_sha": "1111111",
            "target_sha": "2222222",
            "mode": "default",
            "description": "Valid change description",
        },
    ).json()["run_id"]

    raw_payload = json.dumps(
        {
            "ref": "refs/heads/main",
            "after": "9999999",
            "repository": {"full_name": "aruchimo/demo-python-repo"},
        }
    ).encode("utf-8")
    valid_sig = (
        "sha256="
        + hmac.new(
            settings.github_webhook_secret.encode("utf-8"),
            raw_payload,
            hashlib.sha256,
        ).hexdigest()
    )

    # 1. Неверная подпись -> 400
    bad_resp = client.post(
        "/webhooks/github",
        content=raw_payload,
        headers={
            "X-Hub-Signature-256": "sha256=invalid",
            "X-GitHub-Delivery": "deliv-1",
            "X-GitHub-Event": "push",
        },
    )
    assert bad_resp.status_code == 400

    # 2. Верная подпись -> сохраняется в БД, обрабатывается воркером и переводит Run в stale (409)
    ok_resp = client.post(
        "/webhooks/github",
        content=raw_payload,
        headers={
            "X-Hub-Signature-256": valid_sig,
            "X-GitHub-Delivery": "deliv-1",
            "X-GitHub-Event": "push",
        },
    )
    assert ok_resp.status_code == 204
    assert "deliv-1" in task_dispatcher.dispatched_webhooks

    stale_status = client.get(f"/runs/{run_id}/status", headers=headers)
    assert stale_status.status_code == 409
    assert stale_status.json()["error_code"] == "stale_snapshot"

    # 3. Повторная доставка того же delivery_id отклоняется через БД -> 409
    dup_resp = client.post(
        "/webhooks/github",
        content=raw_payload,
        headers={
            "X-Hub-Signature-256": valid_sig,
            "X-GitHub-Delivery": "deliv-1",
            "X-GitHub-Event": "push",
        },
    )
    assert dup_resp.status_code == 409
