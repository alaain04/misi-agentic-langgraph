import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

# Provide dummy env vars before the app is imported so pydantic-settings doesn't error
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("MONGODB_URI", "mongodb://localhost:27017")

from src.api.dependencies import get_job_repo  # noqa: E402
from src.main import app  # noqa: E402

_VALID_PAYLOAD = {
    "repo_url": "https://github.com/example/repo",
    "concern": "security",
}


def _make_mock_dao():
    dao = MagicMock()
    dao.create = AsyncMock(return_value=None)
    return dao


@pytest.fixture
def mock_dao():
    dao = _make_mock_dao()
    app.dependency_overrides[get_job_repo] = lambda: dao
    with patch("src.api.routes.run_analysis", new_callable=AsyncMock):
        yield dao
    app.dependency_overrides.clear()


@pytest.fixture
def client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_analyze_returns_trace_id(mock_dao, client):
    async with client as c:
        response = await c.post("/analyze", json=_VALID_PAYLOAD)
    assert response.status_code == 202
    body = response.json()
    assert "trace_id" in body
    assert body["status"] == "pending"
    assert len(body["trace_id"]) == 24


@pytest.mark.asyncio
async def test_analyze_persists_job(mock_dao, client):
    payload = {**_VALID_PAYLOAD, "concern": "perf"}
    async with client as c:
        await c.post("/analyze", json=payload)
    mock_dao.create.assert_awaited_once()
    job = mock_dao.create.call_args[0][0]
    assert job.metadata.concern == "perf"
    assert job.status == "pending"


@pytest.mark.asyncio
async def test_analyze_with_concern(mock_dao, client):
    payload = {**_VALID_PAYLOAD, "concern": "auth"}
    async with client as c:
        response = await c.post("/analyze", json=payload)
    assert response.status_code == 202


@pytest.mark.asyncio
async def test_analyze_missing_repo_url(mock_dao, client):
    payload = {"concern": "security"}
    async with client as c:
        response = await c.post("/analyze", json=payload)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_analyze_missing_concern(mock_dao, client):
    payload = {"repo_url": "https://github.com/example/repo"}
    async with client as c:
        response = await c.post("/analyze", json=payload)
    assert response.status_code == 422
