# tests/unit/nodes/test_planner.py
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.main_graph.nodes.planner import _FALLBACK_PLAN, run_planner


def _make_state(
    components: list[dict], concern: str = "security", summary: str = "ok"
) -> dict:
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
async def test_planner_returns_plan_object():
    components = [{"name": "requests"}, {"name": "flask"}]
    state = _make_state(components)

    mock_response = MagicMock()
    mock_response.content = json.dumps(
        {"subgraphs": ["vulnerabilities", "registry"], "dep_filter": None}
    )

    with patch("src.main_graph.nodes.planner._llm") as mock_llm:
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)
        plan = await run_planner(state)

    assert plan["subgraphs"] == ["vulnerabilities", "registry"]
    assert plan["dep_filter"] is None


@pytest.mark.asyncio
async def test_planner_accepts_legacy_list_response():
    """LLM returning a plain list is accepted and wrapped in Plan."""
    state = _make_state([{"name": "lodash"}])

    mock_response = MagicMock()
    mock_response.content = json.dumps(["vulnerabilities"])

    with patch("src.main_graph.nodes.planner._llm") as mock_llm:
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)
        plan = await run_planner(state)

    assert plan["subgraphs"] == ["vulnerabilities"]


@pytest.mark.asyncio
async def test_planner_falls_back_on_bad_json():
    state = _make_state([{"name": "lodash"}])

    mock_response = MagicMock()
    mock_response.content = "not json at all"

    with patch("src.main_graph.nodes.planner._llm") as mock_llm:
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)
        plan = await run_planner(state)

    assert plan == _FALLBACK_PLAN


@pytest.mark.asyncio
async def test_planner_includes_dep_filter():
    state = _make_state([{"name": "axios"}])

    mock_response = MagicMock()
    mock_response.content = json.dumps(
        {"subgraphs": ["registry", "repo"], "dep_filter": ["axios"]}
    )

    with patch("src.main_graph.nodes.planner._llm") as mock_llm:
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)
        plan = await run_planner(state, extra_instructions="only check axios")

    assert plan["dep_filter"] == ["axios"]
