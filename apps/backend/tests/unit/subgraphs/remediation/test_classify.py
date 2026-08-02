from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.main_graph.subgraphs.remediation.classify import (
    TargetClassification,
    classify_target,
)
from src.models.remediation import RemediationTarget


@pytest.mark.asyncio
async def test_classify_target_returns_llm_classification():
    target = RemediationTarget(
        target_dep="lodash", addresses=["lodash"], current_range="^4.17.11"
    )
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        return_value=TargetClassification(tier="r1", rationale="patch release only")
    )
    with (
        patch(
            "src.main_graph.subgraphs.remediation.classify.fetch_release_notes",
            AsyncMock(return_value={"available": False}),
        ),
        patch("src.main_graph.subgraphs.remediation.classify._llm", mock_llm),
    ):
        result = await classify_target(
            target, "/tmp/repo", MagicMock(), "node:lts-alpine"
        )

    assert result.tier == "r1"
    mock_llm.with_structured_output.assert_called_once()


@pytest.mark.asyncio
async def test_classify_target_defaults_to_r2_on_llm_exception():
    """A classification failure must degrade to a conservative default
    (r2: assume breaking, needs review) rather than crashing the whole
    classify_targets_node and thus the job."""
    target = RemediationTarget(target_dep="lodash", addresses=["lodash"])
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        side_effect=RuntimeError("LLM provider timeout")
    )
    with (
        patch(
            "src.main_graph.subgraphs.remediation.classify.fetch_release_notes",
            AsyncMock(return_value={"available": False}),
        ),
        patch("src.main_graph.subgraphs.remediation.classify._llm", mock_llm),
    ):
        result = await classify_target(
            target, "/tmp/repo", MagicMock(), "node:lts-alpine"
        )

    assert result.tier == "r2"
