from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.main_graph.nodes.risk_score import risk_score

_FAKE_CONFIG = {"configurable": {"job_repo": MagicMock(), "ingestion_daos": {}}}


def _base_state():
    return {
        "plan": {"subgraphs": ["vulnerabilities", "registry"], "dep_filter": None},
        "sbom_cyclonedx": {
            "components": [
                {"name": "express", "version": "4.18.2"},
                {"name": "lodash", "version": "4.17.21"},
            ]
        },
        "risk_rankings": [
            {
                "dep_name": "express",
                "preliminary_score": 7.5,
                "risk_signals": ["CVE-2023-1234 HIGH"],
                "rationale": "has high CVE",
            },
            {
                "dep_name": "lodash",
                "preliminary_score": 3.0,
                "risk_signals": [],
                "rationale": "low risk",
            },
        ],
        "subgraph_results": [],
        "messages": [],
    }


@pytest.mark.asyncio
async def test_risk_score_calls_agent_and_returns_fallback_on_empty_scores():
    """When mock agent doesn't call save_risk_score, fallback stub data is returned."""
    with patch("src.main_graph.nodes.risk_score.create_agent") as mock_factory:
        mock_agent = MagicMock()
        mock_agent.ainvoke = AsyncMock(return_value={"messages": []})
        mock_factory.return_value = mock_agent

        result = await risk_score(_base_state(), _FAKE_CONFIG)

    mock_factory.assert_called_once()
    assert "risk_scores" in result
    assert len(result["risk_scores"]) == 2  # one per SBOM component
    dep_names = {s["dep_name"] for s in result["risk_scores"]}
    assert dep_names == {"express", "lodash"}
    # fallback scores
    assert all(s["score"] == 5.0 for s in result["risk_scores"])
    assert all(s["severity"] == "medium" for s in result["risk_scores"])
    assert all(s["impact_weight"] is None for s in result["risk_scores"])


@pytest.mark.asyncio
async def test_risk_score_falls_back_on_agent_exception():
    with patch("src.main_graph.nodes.risk_score.create_agent") as mock_factory:
        mock_factory.side_effect = RuntimeError("LLM unavailable")

        result = await risk_score(_base_state(), _FAKE_CONFIG)

    assert len(result["risk_scores"]) == 2
    assert all(s["score"] == 5.0 for s in result["risk_scores"])


@pytest.mark.asyncio
async def test_risk_score_fills_stubs_for_all_deps_in_scope():
    """Even if agent scored some deps, stubs fill in any missing."""
    state = _base_state()

    with patch("src.main_graph.nodes.risk_score.create_agent") as mock_factory:
        mock_agent = MagicMock()
        mock_agent.ainvoke = AsyncMock(return_value={"messages": []})
        mock_factory.return_value = mock_agent

        result = await risk_score(state, _FAKE_CONFIG)

    # Both deps must appear in output even though mock agent didn't call save_risk_score
    dep_names = {s["dep_name"] for s in result["risk_scores"]}
    assert dep_names == {"express", "lodash"}


@pytest.mark.asyncio
async def test_risk_score_empty_sbom_returns_empty_scores():
    state = _base_state()
    state["sbom_cyclonedx"] = {}

    with patch("src.main_graph.nodes.risk_score.create_agent") as mock_factory:
        mock_agent = MagicMock()
        mock_agent.ainvoke = AsyncMock(return_value={"messages": []})
        mock_factory.return_value = mock_agent

        result = await risk_score(state, _FAKE_CONFIG)

    assert result["risk_scores"] == []
