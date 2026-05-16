import pytest
from unittest.mock import AsyncMock, patch

from src.jobs import create, delete, get_status, record_failure, record_success


async def test_create_inserts_correct_document():
    mock_col = AsyncMock()
    with patch("src.jobs._col", return_value=mock_col):
        await create("job-1", ["react", "lodash"])

    mock_col.insert_one.assert_called_once()
    doc = mock_col.insert_one.call_args[0][0]
    assert doc["_id"] == "job-1"
    assert doc["packages"] == ["react", "lodash"]
    assert doc["total"] == 2
    assert doc["completed"] == 0
    assert doc["failed"] == 0
    assert doc["status"] == "pending"
    assert "created_at" in doc
    assert "updated_at" in doc


async def test_record_success_always_calls_update_one():
    mock_col = AsyncMock()
    mock_col.find_one_and_update = AsyncMock(return_value=None)
    with patch("src.jobs._col", return_value=mock_col):
        await record_success("job-1")
    mock_col.update_one.assert_called_once()


async def test_record_failure_always_calls_update_one():
    mock_col = AsyncMock()
    mock_col.find_one_and_update = AsyncMock(return_value=None)
    with patch("src.jobs._col", return_value=mock_col):
        await record_failure("job-1")
    mock_col.update_one.assert_called_once()


async def test_get_status_returns_none_for_missing_job():
    mock_col = AsyncMock()
    mock_col.find_one = AsyncMock(return_value=None)
    with patch("src.jobs._col", return_value=mock_col):
        result = await get_status("missing")
    assert result is None


async def test_get_status_returns_dict():
    mock_col = AsyncMock()
    mock_col.find_one = AsyncMock(
        return_value={"status": "done", "total": 2, "completed": 2, "failed": 0}
    )
    with patch("src.jobs._col", return_value=mock_col):
        result = await get_status("job-1")
    assert result == {"job_id": "job-1", "status": "done", "total": 2, "completed": 2, "failed": 0}


async def test_get_status_uses_projection():
    mock_col = AsyncMock()
    mock_col.find_one = AsyncMock(return_value=None)
    with patch("src.jobs._col", return_value=mock_col):
        await get_status("job-1")
    call_args = mock_col.find_one.call_args
    projection = call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get("projection")
    assert projection is not None
    assert "_id" not in projection or projection.get("_id") == 0
