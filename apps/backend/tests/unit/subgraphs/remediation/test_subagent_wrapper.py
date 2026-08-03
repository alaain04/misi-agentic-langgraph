from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.main_graph.subgraphs.remediation.deepagent.subagent_wrapper import (
    build_codemod_subagent,
    build_replacement_subagent,
)


def test_build_codemod_subagent_shape():
    sub = build_codemod_subagent("/tmp/work", MagicMock(), "img", "npm")
    assert sub["name"] == "codemod_adapter"
    assert "runnable" in sub and sub["description"]


def test_build_replacement_subagent_shape():
    sub = build_replacement_subagent("/tmp/work", MagicMock(), "img", "npm")
    assert sub["name"] == "replacement_migrator"
    assert "runnable" in sub and sub["description"]


@pytest.mark.asyncio
async def test_replacement_subagent_reports_skipped_outcome():
    sub = build_replacement_subagent("/tmp/work", MagicMock(), "img", "npm")
    result = await sub["runnable"].ainvoke(
        {"messages": [{"role": "user", "content": "x"}]}
    )
    outcome = result["structured_response"]
    assert outcome.status == "skipped"
    assert outcome.strategy == "replace"
    assert outcome.skip_reason
