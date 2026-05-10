from unittest.mock import AsyncMock, MagicMock, patch

import pytest


async def test_create_inserts_correct_document():
    mock_col = AsyncMock()
    with patch("src.jobs._col", return_value=mock_col):
        from src.jobs import create
        await create("job-1", ["react", "lodash"])

    mock_col.insert_one.assert_called_once()
    doc = mock_col.insert_one.call_args[0][0]
    assert doc["_id"] == "job-1"
    assert doc["packages"] == ["react", "lodash"]
    assert doc["total"] == 2
    assert doc["completed"] == 0
    assert doc["failed"] == 0
    assert doc["status"] == "pending"


async def test_record_success_sets_done_when_all_complete():
    mock_col = AsyncMock()
    # find_one_and_update returns the updated doc (after increment)
    mock_col.find_one_and_update = AsyncMock(
        return_value={"completed": 1, "failed": 0, "total": 1}
    )
    with patch("src.jobs._col", return_value=mock_col):
        from src.jobs import record_success
        await record_success("job-1")

    # second update sets status=done
    mock_col.update_one.assert_called_once()
    update_args = mock_col.update_one.call_args[0]
    assert update_args[1]["$set"]["status"] == "done"


async def test_record_success_does_not_set_done_when_incomplete():
    mock_col = AsyncMock()
    mock_col.find_one_and_update = AsyncMock(
        return_value={"completed": 1, "failed": 0, "total": 3}
    )
    with patch("src.jobs._col", return_value=mock_col):
        from src.jobs import record_success
        await record_success("job-1")

    mock_col.update_one.assert_not_called()


async def test_get_status_returns_none_for_missing_job():
    mock_col = AsyncMock()
    mock_col.find_one = AsyncMock(return_value=None)
    with patch("src.jobs._col", return_value=mock_col):
        from src.jobs import get_status
        result = await get_status("missing")

    assert result is None


async def test_get_status_returns_dict():
    mock_col = AsyncMock()
    mock_col.find_one = AsyncMock(
        return_value={
            "_id": "job-1",
            "status": "done",
            "total": 2,
            "completed": 2,
            "failed": 0,
        }
    )
    with patch("src.jobs._col", return_value=mock_col):
        from src.jobs import get_status
        result = await get_status("job-1")

    assert result == {
        "job_id": "job-1",
        "status": "done",
        "total": 2,
        "completed": 2,
        "failed": 0,
    }
