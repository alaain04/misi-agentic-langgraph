from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.main_graph.subgraphs.remediation.investigate import investigate_release
from src.models.remediation import ReleaseDigest


@pytest.mark.asyncio
async def test_investigate_release_returns_digest_from_llm():
    notes = {"available": True, "releases": [{"tag": "v2.0.0", "body": "removed foo()"}]}
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        return_value=ReleaseDigest(
            from_version="1.0.0",
            to_version="2.0.0",
            migration_needed=True,
            migration_guide="replace foo() with bar()",
            breaking_changes=["foo() removed"],
        )
    )
    with (
        patch(
            "src.main_graph.subgraphs.remediation.investigate.fetch_release_notes_between",
            AsyncMock(return_value=notes),
        ),
        patch("src.main_graph.subgraphs.remediation.investigate._llm", mock_llm),
    ):
        digest = await investigate_release(
            "lodash", "1.0.0", "2.0.0", "/tmp/repo", MagicMock(), "img"
        )
    assert digest.migration_needed is True
    assert digest.from_version == "1.0.0"
    assert digest.to_version == "2.0.0"


@pytest.mark.asyncio
async def test_investigate_release_conservative_on_failure():
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        side_effect=RuntimeError("LLM timeout")
    )
    with (
        patch(
            "src.main_graph.subgraphs.remediation.investigate.fetch_release_notes_between",
            AsyncMock(return_value={"available": True, "releases": []}),
        ),
        patch("src.main_graph.subgraphs.remediation.investigate._llm", mock_llm),
    ):
        digest = await investigate_release(
            "lodash", "1.0.0", "2.0.0", "/tmp/repo", MagicMock(), "img"
        )
    assert digest.migration_needed is True  # conservative default
    assert digest.breaking_changes  # carries an explanatory reason


@pytest.mark.asyncio
async def test_investigate_release_conservative_when_notes_unavailable():
    mock_llm = MagicMock()
    with (
        patch(
            "src.main_graph.subgraphs.remediation.investigate.fetch_release_notes_between",
            AsyncMock(return_value={"available": False, "error": "gh CLI not found"}),
        ),
        patch("src.main_graph.subgraphs.remediation.investigate._llm", mock_llm),
    ):
        digest = await investigate_release(
            "lodash", "1.0.0", "2.0.0", "/tmp/repo", MagicMock(), "img"
        )
    assert digest.migration_needed is True
    assert digest.breaking_changes
    assert "unavailable" in digest.breaking_changes[0]
    # LLM should not have been consulted for unavailable notes.
    mock_llm.with_structured_output.assert_not_called()
