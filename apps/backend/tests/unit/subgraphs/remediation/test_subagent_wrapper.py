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
    # the role tag rides on the model instance itself, so the compiled agent is
    # returned unwrapped -- callers get a directly invocable graph
    assert "agent_role:remediation_execution_deepagent" in (kwargs["model"].tags or [])
    mock_create.return_value.with_config.assert_not_called()
    assert agent is mock_create.return_value
