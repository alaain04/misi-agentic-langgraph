# tests/unit/nodes/test_planner.py
import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from src.main_graph.nodes.planner import run_planner


def _make_state(components: list[dict], concern: str = "security", summary: str = "ok") -> dict:
    return {
        "job_id": "j1",
        "concern": concern,
        "discovery_summary": summary,
        "sbom_cyclonedx": {"components": components},
        "repo_url": "http://example.com/repo",
        "messages": [],
        "subgraph_results": [],
    }


@pytest.mark.asyncio
async def test_planner_uses_sbom_components():
    components = [{"name": "requests"}, {"name": "flask"}]
    state = _make_state(components)

    mock_response = MagicMock()
    mock_response.content = json.dumps(["vulnerabilities"])

    with patch("src.main_graph.nodes.planner._llm") as mock_llm:
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)
        plan = await run_planner(state)

    call_args = mock_llm.ainvoke.call_args[0][0]
    user_msg = next(m["content"] for m in call_args if m["role"] == "user")
    assert "requests" in user_msg
    assert "flask" in user_msg


@pytest.mark.asyncio
async def test_planner_falls_back_on_bad_json():
    state = _make_state([{"name": "lodash"}])

    mock_response = MagicMock()
    mock_response.content = "not json at all"

    with patch("src.main_graph.nodes.planner._llm") as mock_llm:
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)
        plan = await run_planner(state)

    assert isinstance(plan, list)
    assert len(plan) > 0


@pytest.mark.asyncio
async def test_planner_passes_extra_instructions():
    state = _make_state([{"name": "axios"}])

    mock_response = MagicMock()
    mock_response.content = json.dumps(["vulnerabilities"])

    with patch("src.main_graph.nodes.planner._llm") as mock_llm:
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)
        plan = await run_planner(state, extra_instructions="focus on licenses")

    call_args = mock_llm.ainvoke.call_args[0][0]
    user_msg = next(m["content"] for m in call_args if m["role"] == "user")
    assert "focus on licenses" in user_msg
