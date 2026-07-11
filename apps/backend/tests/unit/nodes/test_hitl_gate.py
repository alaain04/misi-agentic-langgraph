from unittest.mock import AsyncMock, patch

import pytest

from src.main_graph.nodes.hitl_gate import hitl_gate
from src.models.conductor import ConductorDecision


def _make_state(autopilot=False, ask_user=None, checkpoint_message=None):
    decision = ConductorDecision(
        tool_calls=[], findings=[], ask_user=ask_user,
        checkpoint_message=checkpoint_message, finalize=False, reasoning="r",
    )
    return {
        "repo_url": "https://github.com/test/repo",
        "concern": "security",
        "job_id": "j1",
        "autopilot": autopilot,
        "tool_results": [],
        "findings": [],
        "conductor_iteration": 1,
        "messages": [],
        "conductor_decision": decision,
    }


def _make_config(dao=None):
    mock_dao = dao or AsyncMock()
    return {"configurable": {"job_repo": mock_dao}}


@pytest.mark.asyncio
async def test_hitl_gate_passthrough_in_autopilot():
    state = _make_state(autopilot=True, ask_user="what should I do?")
    with patch("src.main_graph.nodes.hitl_gate.interrupt") as mock_interrupt:
        result = await hitl_gate(state, config=_make_config())
    mock_interrupt.assert_not_called()
    assert result == {}


@pytest.mark.asyncio
async def test_hitl_gate_passthrough_when_no_question():
    state = _make_state(autopilot=False, ask_user=None, checkpoint_message=None)
    with patch("src.main_graph.nodes.hitl_gate.interrupt") as mock_interrupt:
        result = await hitl_gate(state, config=_make_config())
    mock_interrupt.assert_not_called()


@pytest.mark.asyncio
async def test_hitl_gate_calls_interrupt_for_ask_user():
    state = _make_state(autopilot=False, ask_user="Can you clarify the concern?")
    mock_dao = AsyncMock()
    with patch("src.main_graph.nodes.hitl_gate.interrupt", return_value="user reply") as mock_interrupt:
        with patch("src.main_graph.nodes.hitl_gate.get_services", return_value={"job_repo": mock_dao}):
            result = await hitl_gate(state, config=_make_config(mock_dao))
    mock_interrupt.assert_called_once()
    assert "messages" in result
