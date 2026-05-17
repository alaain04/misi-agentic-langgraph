from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from api.routers.ingest_router import get_status, ingest
from api.schemas import IngestRequest
from domain.ports.fetcher_port import FetcherPort


def _make_registry() -> dict[str, FetcherPort]:
    return {"npm": MagicMock(), "github_issues": MagicMock()}


async def test_ingest_returns_job_id():
    mock_service = MagicMock()
    mock_service.create_job = AsyncMock(return_value="job-123")
    req = IngestRequest(entity_types=["npm"], items=["react", "lodash"])
    result = await ingest(req, mock_service, _make_registry())
    assert result.job_ids["npm"] == "job-123"
    mock_service.create_job.assert_called_once_with("npm", ["react", "lodash"])


async def test_ingest_rejects_unknown_entity_type():
    mock_service = MagicMock()
    req = IngestRequest(entity_types=["unknown"], items=["foo"])
    with pytest.raises(HTTPException) as exc:
        await ingest(req, mock_service, _make_registry())
    assert exc.value.status_code == 422


async def test_ingest_raises_503_on_publish_failure():
    mock_service = MagicMock()
    mock_service.create_job = AsyncMock(side_effect=Exception("nats down"))
    req = IngestRequest(entity_types=["npm"], items=["react"])
    with pytest.raises(HTTPException) as exc:
        await ingest(req, mock_service, _make_registry())
    assert exc.value.status_code == 503


async def test_status_returns_progress():
    mock_service = MagicMock()
    mock_service.get_status = AsyncMock(
        return_value={
            "job_id": "job-1",
            "status": "running",
            "total": 10,
            "completed": 5,
            "failed": 0,
        }
    )
    result = await get_status("job-1", mock_service)
    assert result.completed == 5


async def test_status_returns_404_for_missing_job():
    mock_service = MagicMock()
    mock_service.get_status = AsyncMock(return_value=None)
    with pytest.raises(HTTPException) as exc:
        await get_status("missing", mock_service)
    assert exc.value.status_code == 404
