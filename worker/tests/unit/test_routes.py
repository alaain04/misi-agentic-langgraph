from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient


async def test_ingest_returns_job_id():
    with (
        patch("src.routers.ingest.jobs.create", new_callable=AsyncMock),
        patch("src.routers.ingest.get_js") as mock_js,
    ):
        mock_js.return_value.publish = AsyncMock()
        from src.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.post(
                "/ingest",
                json={"entity_type": "npm", "items": ["react", "lodash"]},
            )

    assert resp.status_code == 200
    body = resp.json()
    assert "job_id" in body
    assert isinstance(body["job_id"], str)


async def test_status_returns_404_for_unknown_job():
    with patch(
        "src.routers.ingest.jobs.get_status",
        new_callable=AsyncMock,
        return_value=None,
    ):
        from src.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get("/status/unknown-job-id")

    assert resp.status_code == 404


async def test_status_returns_progress():
    status_data = {
        "job_id": "job-1",
        "status": "running",
        "total": 10,
        "completed": 5,
        "failed": 0,
    }
    with patch(
        "src.routers.ingest.jobs.get_status",
        new_callable=AsyncMock,
        return_value=status_data,
    ):
        from src.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get("/status/job-1")

    assert resp.status_code == 200
    assert resp.json()["completed"] == 5
