import pytest
from unittest.mock import AsyncMock, MagicMock

from adapters.db.mongodb.mongo_job_repository import MongoJobRepository


def _make_repo():
    mock_col = AsyncMock()
    mock_db = MagicMock()
    mock_db.__getitem__ = MagicMock(return_value=mock_col)
    return MongoJobRepository(mock_db), mock_col


async def test_create_inserts_correct_document():
    repo, col = _make_repo()
    await repo.create("job-1", ["react", "lodash"])
    col.insert_one.assert_called_once()
    doc = col.insert_one.call_args[0][0]
    assert doc["_id"] == "job-1"
    assert doc["packages"] == ["react", "lodash"]
    assert doc["total"] == 2
    assert doc["completed"] == 0
    assert doc["failed"] == 0
    assert doc["status"] == "pending"
    assert "created_at" in doc
    assert "updated_at" in doc


async def test_record_success_calls_find_one_and_update_and_update_one():
    repo, col = _make_repo()
    await repo.record_success("job-1")
    col.find_one_and_update.assert_called_once()
    col.update_one.assert_called_once()


async def test_record_failure_calls_find_one_and_update_and_update_one():
    repo, col = _make_repo()
    await repo.record_failure("job-1")
    col.find_one_and_update.assert_called_once()
    col.update_one.assert_called_once()


async def test_get_status_returns_none_for_missing():
    repo, col = _make_repo()
    col.find_one = AsyncMock(return_value=None)
    result = await repo.get_status("missing")
    assert result is None


async def test_get_status_returns_dict():
    repo, col = _make_repo()
    col.find_one = AsyncMock(
        return_value={"status": "done", "total": 2, "completed": 2, "failed": 0}
    )
    result = await repo.get_status("job-1")
    assert result == {
        "job_id": "job-1",
        "status": "done",
        "total": 2,
        "completed": 2,
        "failed": 0,
    }


async def test_delete_calls_delete_one():
    repo, col = _make_repo()
    await repo.delete("job-1")
    col.delete_one.assert_called_once_with({"_id": "job-1"})
