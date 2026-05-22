import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import httpx


@pytest.mark.asyncio
async def test_ingest_and_wait_returns_job_ids():
    mock_ingest_resp = MagicMock()
    mock_ingest_resp.json.return_value = {"job_ids": {"npm": "job-1"}}
    mock_ingest_resp.raise_for_status = MagicMock()

    mock_status_resp = MagicMock()
    mock_status_resp.json.return_value = {"status": "done"}
    mock_status_resp.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(return_value=mock_ingest_resp)
    mock_client.get = AsyncMock(return_value=mock_status_resp)

    with patch("src.utils.workers_client.httpx.AsyncClient", return_value=mock_client):
        with patch("src.utils.workers_client.asyncio.sleep", new_callable=AsyncMock):
            from src.utils.workers_client import ingest_and_wait
            result = await ingest_and_wait(["npm"], ["lodash"])

    assert result == {"npm": "job-1"}


@pytest.mark.asyncio
async def test_ingest_and_wait_raises_on_failure():
    mock_ingest_resp = MagicMock()
    mock_ingest_resp.json.return_value = {"job_ids": {"npm": "job-2"}}
    mock_ingest_resp.raise_for_status = MagicMock()

    mock_status_resp = MagicMock()
    mock_status_resp.json.return_value = {"status": "failed"}
    mock_status_resp.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(return_value=mock_ingest_resp)
    mock_client.get = AsyncMock(return_value=mock_status_resp)

    with patch("src.utils.workers_client.httpx.AsyncClient", return_value=mock_client):
        with patch("src.utils.workers_client.asyncio.sleep", new_callable=AsyncMock):
            from src.utils.workers_client import ingest_and_wait
            with pytest.raises(RuntimeError, match="failed"):
                await ingest_and_wait(["npm"], ["lodash"])
