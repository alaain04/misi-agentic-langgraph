from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.main_graph.subgraphs.remediation.deepagent.subagent_wrapper import (
    ExecutionState,
    build_execution_agent,
)


def test_execution_state_declares_outcomes_channel():
    # The execution agent must declare an `outcomes` channel so a
    # commit_outcome Command survives ainvoke and merges up per group.
    assert "outcomes" in ExecutionState.__annotations__


def test_build_execution_agent_returns_directly_invocable_agent():
    with patch(
        "src.main_graph.subgraphs.remediation.deepagent.subagent_wrapper.create_deep_agent"
    ) as mock_create:
        agent = build_execution_agent("/tmp/work", MagicMock(), "img", "npm")

    mock_create.assert_called_once()
    _, kwargs = mock_create.call_args
    assert kwargs["state_schema"] is ExecutionState
    assert len(kwargs["tools"]) == 6
    # never registered as a task()-dispatchable subagent
    assert "subagents" not in kwargs
    # the compiled graph is tagged for role-aware cost tracking, since the
    # model itself can't carry a get_role_llm() tag into create_deep_agent
    mock_create.return_value.with_config.assert_called_once_with(
        tags=["agent_role:remediation_execution_deepagent"]
    )
    assert agent is mock_create.return_value.with_config.return_value
