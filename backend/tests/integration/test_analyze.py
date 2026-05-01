import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

# Provide dummy env vars before the app is imported so pydantic-settings doesn't error
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("MONGODB_URI", "mongodb://localhost:27017")

from src.main import app  # noqa: E402

_VALID_METADATA = {
    "package_json": "{}",
    "lock_file": "",
    "lock_file_name": "package-lock.json",
    "concern": "security",
}


def _make_mock_dao():
    dao = MagicMock()
    dao.create = AsyncMock(return_value=None)
    return dao


@pytest.fixture
def mock_dao():
    dao = _make_mock_dao()
    with (
        patch("src.api.routes.JobDAO", return_value=dao),
        patch("src.api.routes.run_analysis", new_callable=AsyncMock),
    ):
        yield dao


@pytest.fixture
def client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_analyze_returns_trace_id(mock_dao, client):
    async with client as c:
        response = await c.post("/analyze", json={"metadata": _VALID_METADATA})
    assert response.status_code == 202
    body = response.json()
    assert "trace_id" in body
    assert body["status"] == "pending"
    assert len(body["trace_id"]) == 24


@pytest.mark.asyncio
async def test_analyze_persists_job(mock_dao, client):
    payload = {**_VALID_METADATA, "concern": "perf"}
    async with client as c:
        await c.post("/analyze", json={"metadata": payload})
    mock_dao.create.assert_awaited_once()
    job = mock_dao.create.call_args[0][0]
    assert job.metadata.concern == "perf"
    assert job.status == "pending"


@pytest.mark.asyncio
async def test_analyze_with_concern(mock_dao, client):
    payload = {**_VALID_METADATA, "concern": "auth"}
    async with client as c:
        response = await c.post("/analyze", json={"metadata": payload})
    assert response.status_code == 202


@pytest.mark.asyncio
async def test_analyze_invalid_lock_file_name(mock_dao, client):
    payload = {**_VALID_METADATA, "lock_file_name": "bad.lock"}
    async with client as c:
        response = await c.post("/analyze", json={"metadata": payload})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_analyze_missing_concern(mock_dao, client):
    payload = {k: v for k, v in _VALID_METADATA.items() if k != "concern"}
    async with client as c:
        response = await c.post("/analyze", json={"metadata": payload})
    assert response.status_code == 422
