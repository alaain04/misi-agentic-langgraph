import pytest
from unittest.mock import AsyncMock, patch

from httpx import ASGITransport, AsyncClient

from src.main import app


@pytest.mark.asyncio
async def test_ingest_returns_job_id():
    with (
        patch("src.routers.ingest.jobs.create", new_callable=AsyncMock),
        patch("src.routers.ingest.get_js") as mock_js,
    ):
        mock_js.return_value.publish = AsyncMock()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/ingest",
                json={"entity_type": "npm", "items": ["react", "lodash"]},
            )

    assert resp.status_code == 201
    body = resp.json()
    assert "job_id" in body
    assert isinstance(body["job_id"], str)


@pytest.mark.asyncio
async def test_ingest_rejects_unknown_entity_type():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/ingest",
            json={"entity_type": "unknown_type", "items": ["foo"]},
        )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_status_returns_404_for_unknown_job():
    with patch("src.routers.ingest.jobs.get_status", new_callable=AsyncMock, return_value=None):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/status/unknown-job-id")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_status_returns_progress():
    status_data = {
        "job_id": "job-1",
        "status": "running",
        "total": 10,
        "completed": 5,
        "failed": 0,
    }
    with patch("src.routers.ingest.jobs.get_status", new_callable=AsyncMock, return_value=status_data):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/status/job-1")
    assert resp.status_code == 200
    assert resp.json()["completed"] == 5
