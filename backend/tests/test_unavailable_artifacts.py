import asyncio
from uuid import uuid4

import httpx
import pytest

from app.api.router import (
    UploadRequest,
    create_upload,
    get_current_user_id,
    get_db,
    require_offline_demo,
)
from app.core.config import settings
from app.main import app


class FakeSession:
    def __init__(self):
        self.added = []
        self.commits = 0

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.commits += 1


async def request(method, path, **kwargs):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        return await client.request(method, path, **kwargs)


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/repos", None),
        ("POST", "/uploads", {"filename": "requirements.md"}),
        ("GET", "/uploads/00000000-0000-0000-0000-000000000001", None),
        ("POST", "/installations/123/connect", None),
        ("GET", "/runs/00000000-0000-0000-0000-000000000001/export", None),
        ("GET", "/runs/00000000-0000-0000-0000-000000000001/tests", None),
        ("GET", "/runs/00000000-0000-0000-0000-000000000001/scenarios", None),
    ],
)
def test_unavailable_features_return_503_without_side_effects(monkeypatch, method, path, body):
    monkeypatch.setattr(settings, "offline_demo_mode", False)
    db = FakeSession()
    app.dependency_overrides[get_current_user_id] = uuid4
    app.dependency_overrides[get_db] = lambda: db
    try:
        response = asyncio.run(asyncio.wait_for(request(method, path, json=body), timeout=5))
        assert response.status_code == 503
        assert response.json()["error_code"] == "feature_unavailable"
        assert db.added == []
        assert db.commits == 0
    finally:
        app.dependency_overrides.clear()


def test_demo_can_use_upload(monkeypatch):
    monkeypatch.setattr(settings, "offline_demo_mode", True)
    db = FakeSession()
    assert asyncio.run(require_offline_demo()) is None
    result = create_upload(UploadRequest(filename="req.md"), user_id=uuid4(), db=db)
    assert result["status"] == "ready"
    assert len(db.added) == 1
    assert db.commits == 1
