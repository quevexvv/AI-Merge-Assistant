from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import Repository, Run, User, WebhookDelivery
from app.worker import tasks


@pytest.mark.parametrize(
    ("repository", "sha", "expected"),
    [
        ({"full_name": "someone/unknown"}, "3" * 40, ("current", "pending")),
        ({}, "3" * 40, ("current", "pending")),
        ({"full_name": "owner/repo"}, "1" * 40, ("stale", "conflict")),
        ({"full_name": "owner/repo"}, None, ("stale", "conflict")),
    ],
)
def test_webhook_scopes_and_conservatively_invalidates(monkeypatch, repository, sha, expected):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(tasks, "SessionLocal", sessions)
    run_id = uuid4()
    with sessions.begin() as db:
        user = User(id=uuid4(), github_login="owner", github_id="1")
        repo = Repository(
            id=uuid4(),
            github_repo_id="1",
            owner_login="owner",
            repo_name="repo",
            full_name="owner/repo",
        )
        db.add_all([user, repo])
        db.add(
            Run(
                id=run_id,
                user_id=user.id,
                source_type="github",
                repo_id=repo.id,
                source_sha="1" * 40,
                target_sha="2" * 40,
                execution_state="completed",
                freshness="current",
                merge_status="pending",
                mode="default",
                environment_profile="py313",
                dependency_profile="default",
            )
        )
        db.add(WebhookDelivery(delivery_id="delivery", event_type="push", payload_hash="hash"))
    result = tasks.process_webhook_delivery_task.run(
        "delivery", "push", {"repository": repository, "after": sha}
    )
    with sessions() as db:
        run = db.get(Run, run_id)
        assert (run.freshness, run.merge_status) == expected
        assert db.query(WebhookDelivery).one().processed is True
    assert result["status"] == "processed"
    engine.dispose()
