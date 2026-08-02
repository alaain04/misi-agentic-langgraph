from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.main_graph.constants import ANALYSIS
from src.main_graph.subgraphs.analysis.nodes.handle_invalid_concern import (
    INVALID_CONCERN_MESSAGE,
    handle_invalid_concern,
)


@pytest.mark.asyncio
async def test_handle_invalid_concern_writes_message_and_sets_no_result_id():
    fake_job_repo = MagicMock()
    fake_job_repo.update_artifact_data = AsyncMock()
    mock_get_services = MagicMock(return_value={"job_repo": fake_job_repo})

    with patch(
        "src.main_graph.subgraphs.analysis.nodes.handle_invalid_concern.get_services",
        mock_get_services,
    ):
        result = await handle_invalid_concern(
            {"job_id": "job-1", "concern": "hello"},
            {"configurable": {}},
        )

    # No analysis_result_id -- this is what makes main_graph's existing
    # _after_analysis routing (`if not analysis_result_id: return END`) skip
    # remediation/report for this job.
    assert result == {}

    fake_job_repo.update_artifact_data.assert_awaited_once_with(
        "job-1", ANALYSIS, {"message": INVALID_CONCERN_MESSAGE}
    )
