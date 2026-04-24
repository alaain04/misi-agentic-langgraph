import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

# Provide dummy env vars before the app is imported so pydantic-settings doesn't error
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("MONGODB_URI", "mongodb://localhost:27017")

from src.main import app  # noqa: E402


def _make_mock_db():
    collection = MagicMock()
    collection.insert_one = AsyncMock(return_value=MagicMock(inserted_id="fake-id"))
    db = MagicMock()
    db.__getitem__ = MagicMock(return_value=collection)
    return db


@pytest.fixture
def mock_db():
    db = _make_mock_db()
    with patch("src.api.routes.get_db", return_value=db):
        yield db


@pytest.fixture
def client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_analyze_returns_job_id(mock_db, client):
    async with client as c:
        response = await c.post(
            "/analyze",
            json={"repo_url": "https://github.com/org/repo", "concern": "security"},
        )
    assert response.status_code == 201
    body = response.json()
    assert "job_id" in body
    assert body["status"] == "pending"
    assert len(body["job_id"]) == 24


@pytest.mark.asyncio
async def test_analyze_persists_job(mock_db, client):
    async with client as c:
        await c.post(
            "/analyze",
            json={"repo_url": "https://github.com/org/repo", "concern": "perf"},
        )
    collection = mock_db["jobs"]
    collection.insert_one.assert_awaited_once()
    doc = collection.insert_one.call_args[0][0]
    assert doc["concern"] == "perf"
    assert doc["status"] == "pending"
    assert "_id" in doc


@pytest.mark.asyncio
async def test_analyze_with_token(mock_db, client):
    async with client as c:
        response = await c.post(
            "/analyze",
            json={
                "repo_url": "https://github.com/org/private-repo",
                "concern": "auth",
                "token": "ghp_secret",
            },
        )
    assert response.status_code == 201
    collection = mock_db["jobs"]
    doc = collection.insert_one.call_args[0][0]
    assert doc["token"] == "ghp_secret"


@pytest.mark.asyncio
async def test_analyze_invalid_url(mock_db, client):
    async with client as c:
        response = await c.post(
            "/analyze",
            json={"repo_url": "not-a-url", "concern": "security"},
        )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_analyze_missing_concern(mock_db, client):
    async with client as c:
        response = await c.post(
            "/analyze",
            json={"repo_url": "https://github.com/org/repo"},
        )
    assert response.status_code == 422
