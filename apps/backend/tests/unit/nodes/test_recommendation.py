from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.main_graph.nodes.recommendation import recommendation


def _base_state():
    return {
        "high_risk_deps": ["express", "cookie"],
        "risk_scores": [
            {
                "dep_name": "express",
                "score": 7.5,
                "severity": "high",
                "breakdown": {"vulnerabilities": 4.0},
                "rationale": "has high CVE",
                "impact_weight": 0.5,
            },
            {
                "dep_name": "cookie",
                "score": 6.0,
                "severity": "high",
                "breakdown": {"maintenance": 2.5},
                "rationale": "deprecated",
                "impact_weight": None,
            },
        ],
        "subgraph_results": [],
        "messages": [],
    }


@pytest.mark.asyncio
async def test_recommendation_calls_agent_and_returns_fallback_on_empty():
    """When mock agent doesn't call save_recommendation, fallback stubs returned."""
    with patch(
        "src.main_graph.nodes.recommendation.create_agent"
    ) as mock_factory:
        mock_agent = MagicMock()
        mock_agent.ainvoke = AsyncMock(return_value={"messages": []})
        mock_factory.return_value = mock_agent

        result = await recommendation(_base_state())

    mock_factory.assert_called_once()
    assert "recommendations" in result
    assert len(result["recommendations"]) == 2
    dep_names = {r["dep_name"] for r in result["recommendations"]}
    assert dep_names == {"express", "cookie"}
    # fallback has empty alternatives
    assert all(r["alternatives"] == [] for r in result["recommendations"])


@pytest.mark.asyncio
async def test_recommendation_falls_back_on_agent_exception():
    with patch(
        "src.main_graph.nodes.recommendation.create_agent"
    ) as mock_factory:
        mock_factory.side_effect = RuntimeError("LLM unavailable")

        result = await recommendation(_base_state())

    assert len(result["recommendations"]) == 2
    assert all(r["alternatives"] == [] for r in result["recommendations"])


@pytest.mark.asyncio
async def test_recommendation_empty_high_risk_deps_returns_empty():
    state = _base_state()
    state["high_risk_deps"] = []

    with patch(
        "src.main_graph.nodes.recommendation.create_agent"
    ) as mock_factory:
        mock_agent = MagicMock()
        mock_agent.ainvoke = AsyncMock(return_value={"messages": []})
        mock_factory.return_value = mock_agent

        result = await recommendation(state)

    assert result["recommendations"] == []
    # agent should not be called when there's nothing to do
    mock_factory.assert_not_called()


@pytest.mark.asyncio
async def test_recommendation_ainvoke_exception_returns_fallback():
    with patch(
        "src.main_graph.nodes.recommendation.create_agent"
    ) as mock_factory:
        mock_agent = MagicMock()
        mock_agent.ainvoke = AsyncMock(side_effect=RuntimeError("recursion limit"))
        mock_factory.return_value = mock_agent

        result = await recommendation(_base_state())

    assert len(result["recommendations"]) == 2
    assert all(r["alternatives"] == [] for r in result["recommendations"])
