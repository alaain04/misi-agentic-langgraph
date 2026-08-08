from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

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


def test_codemod_subagent_can_emit_outcomes_channel():
    # The codemod subagent must declare an `outcomes` channel so a
    # commit_outcome Command survives ainvoke and merges up to the root.
    from src.main_graph.subgraphs.remediation.deepagent.subagent_wrapper import (
        _CodemodState,
    )

    assert "outcomes" in _CodemodState.__annotations__


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


@pytest.mark.asyncio
async def test_semaphore_caps_concurrent_codemod_runs():
    """Without TARGET_SEMAPHORE, the planner's task() fan-out to
    codemod_adapter is unbounded and each dispatched codemod task runs its
    own multi-turn LLM loop -- the actual mechanism behind the rate-limit
    exhaustion this guards against. Verify concurrent codemod_adapter
    invocations are capped at the semaphore's size regardless of how many
    are launched in parallel."""
    concurrent = 0
    peak = 0

    async def _slow_ainvoke(*args, **kwargs):
        nonlocal concurrent, peak
        concurrent += 1
        peak = max(peak, concurrent)
        await asyncio.sleep(0.02)
        concurrent -= 1
        return {"messages": [], "outcomes": {}}

    test_semaphore = asyncio.Semaphore(2)

    with (
        patch(
            "src.main_graph.subgraphs.remediation.deepagent.subagent_wrapper.TARGET_SEMAPHORE",
            test_semaphore,
        ),
        patch(
            "src.main_graph.subgraphs.remediation.deepagent.subagent_wrapper.create_deep_agent"
        ) as mock_create,
    ):
        nested_agent = AsyncMock()
        nested_agent.ainvoke = _slow_ainvoke
        mock_create.return_value = nested_agent

        sub = build_codemod_subagent("/tmp/work", MagicMock(), "img", "npm")

        await asyncio.gather(
            *[
                sub["runnable"].ainvoke(
                    {"messages": [{"role": "user", "content": "eslint"}]}
                )
                for _ in range(5)
            ]
        )

    assert peak <= 2
