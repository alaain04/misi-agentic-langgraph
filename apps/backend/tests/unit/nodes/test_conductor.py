from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from src.main_graph.nodes.conductor import conductor, _format_messages
from src.models.conductor import ConductorDecision, FindingNote, ToolCall


def _make_state(**kwargs):
    defaults = {
        "repo_url": "https://github.com/test/repo",
        "concern": "security vulnerabilities",
        "job_id": "job-1",
        "autopilot": False,
        "project_context": "A Node.js API with lodash and express",
        "detected_package_manager": "npm",
        "tool_results": [],
        "findings": [],
        "conductor_iteration": 0,
        "messages": [],
    }
    return {**defaults, **kwargs}


@pytest.mark.asyncio
async def test_conductor_increments_iteration():
    decision = ConductorDecision(
        tool_calls=[ToolCall(tool="npm_audit", args={}, reason="check")],
        findings=[], ask_user=None, checkpoint_message=None, finalize=False, reasoning="r",
    )
    with patch("src.main_graph.nodes.conductor._llm") as mock_llm:
        mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(return_value=decision)
        result = await conductor(_make_state(), config={"configurable": {}})
    assert result["conductor_iteration"] == 1


@pytest.mark.asyncio
async def test_conductor_forces_finalize_at_max_iterations():
    decision = ConductorDecision(
        tool_calls=[ToolCall(tool="npm_audit", args={}, reason="check")],
        findings=[], ask_user=None, checkpoint_message=None, finalize=False, reasoning="r",
    )
    with patch("src.main_graph.nodes.conductor._llm") as mock_llm:
        mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(return_value=decision)
        state = _make_state(conductor_iteration=9)
        result = await conductor(state, config={"configurable": {}})
    assert result["conductor_decision"].finalize is True


@pytest.mark.asyncio
async def test_conductor_suppresses_ask_user_in_autopilot():
    decision = ConductorDecision(
        tool_calls=[], findings=[], ask_user="can you clarify?",
        checkpoint_message=None, finalize=False, reasoning="r",
    )
    with patch("src.main_graph.nodes.conductor._llm") as mock_llm:
        mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(return_value=decision)
        result = await conductor(_make_state(autopilot=True), config={"configurable": {}})
    assert result["conductor_decision"].ask_user is None
    assert result["conductor_decision"].checkpoint_message is None


@pytest.mark.asyncio
async def test_conductor_accumulates_findings():
    new_finding = FindingNote(dep_name="lodash", severity="high", description="vuln", evidence=[])
    decision = ConductorDecision(
        tool_calls=[], findings=[new_finding], ask_user=None,
        checkpoint_message=None, finalize=True, reasoning="r",
    )
    with patch("src.main_graph.nodes.conductor._llm") as mock_llm:
        mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(return_value=decision)
        result = await conductor(_make_state(), config={"configurable": {}})
    assert len(result["findings"]) == 1
    assert result["findings"][0].dep_name == "lodash"


def test_format_messages_returns_placeholder_when_empty():
    assert _format_messages([]) == "No conversation history."


def test_format_messages_labels_assistant_and_user():
    msgs = [AIMessage(content="Shall I proceed?"), HumanMessage(content="Yes, continue.")]
    result = _format_messages(msgs)
    assert "[assistant]: Shall I proceed?" in result
    assert "[user]: Yes, continue." in result


@pytest.mark.asyncio
async def test_conductor_includes_conversation_content_in_prompt():
    """When state has messages, conductor prompt includes their text, not just a count."""
    decision = ConductorDecision(
        tool_calls=[], findings=[], ask_user=None, checkpoint_message=None,
        finalize=True, reasoning="done",
    )
    msgs = [AIMessage(content="Proceed to report?"), HumanMessage(content="Yes please")]
    captured: list = []

    async def capture_invoke(messages, **_):
        captured.extend(messages)
        return decision

    with patch("src.main_graph.nodes.conductor._llm") as mock_llm:
        mock_llm.with_structured_output.return_value.ainvoke = capture_invoke
        await conductor(_make_state(messages=msgs), config={"configurable": {}})

    full_text = "\n".join(
        m["content"] if isinstance(m, dict) else str(m.content)
        for m in captured
    )
    assert "Proceed to report?" in full_text
    assert "Yes please" in full_text
