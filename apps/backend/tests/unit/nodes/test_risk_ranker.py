from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.main_graph.nodes.risk_ranker import _select_high_risk, risk_ranker, risk_ranker_router


def _base_state():
    return {
        "plan": {"subgraphs": ["vulnerabilities", "registry"], "dep_filter": None},
        "sbom_cyclonedx": {
            "components": [
                {"name": "express", "version": "4.18.2"},
                {"name": "lodash", "version": "4.17.21"},
                {"name": "cookie", "version": "0.5.0"},
            ]
        },
        "subgraph_results": [],
        "execution_stages": [],
        "messages": [],
    }


# ── _select_high_risk ────────────────────────────────────────────────────────

def test_select_high_risk_returns_top3():
    rankings = [
        {"dep_name": "A", "preliminary_score": 9.0, "risk_signals": []},
        {"dep_name": "B", "preliminary_score": 8.0, "risk_signals": []},
        {"dep_name": "C", "preliminary_score": 7.0, "risk_signals": []},
        {"dep_name": "D", "preliminary_score": 2.0, "risk_signals": []},
    ]
    result = _select_high_risk(rankings)
    assert set(result) == {"A", "B", "C"}
    assert "D" not in result


def test_select_high_risk_includes_high_score_beyond_top3():
    rankings = [
        {"dep_name": "A", "preliminary_score": 9.0, "risk_signals": []},
        {"dep_name": "B", "preliminary_score": 8.5, "risk_signals": []},
        {"dep_name": "C", "preliminary_score": 8.0, "risk_signals": []},
        {"dep_name": "D", "preliminary_score": 7.5, "risk_signals": []},  # score >= 7.0 but not top-3
        {"dep_name": "E", "preliminary_score": 1.0, "risk_signals": []},
    ]
    result = _select_high_risk(rankings)
    assert "D" in result  # score >= 7.0
    assert "E" not in result


def test_select_high_risk_empty_returns_empty():
    assert _select_high_risk([]) == []


# ── risk_ranker node ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_risk_ranker_calls_agent_and_returns_fallback_on_empty_rankings():
    """When mock agent doesn't call save_ranking, fallback stub data is returned."""
    with patch(
        "src.main_graph.nodes.risk_ranker.create_agent"
    ) as mock_factory:
        mock_agent = MagicMock()
        mock_agent.ainvoke = AsyncMock(return_value={"messages": []})
        mock_factory.return_value = mock_agent

        result = await risk_ranker(_base_state())

    mock_factory.assert_called_once()
    assert result["risk_ranker_done"] is True
    assert len(result["risk_rankings"]) == 3  # one per SBOM component
    assert result["risk_rankings"][0]["dep_name"] in {"express", "lodash", "cookie"}
    assert result["risk_rankings"][0]["preliminary_score"] == 5.0
    # high_risk_deps = top-3 of 3 deps
    assert len(result["high_risk_deps"]) == 3


@pytest.mark.asyncio
async def test_risk_ranker_falls_back_on_agent_exception():
    with patch(
        "src.main_graph.nodes.risk_ranker.create_agent"
    ) as mock_factory:
        mock_factory.side_effect = RuntimeError("LLM unavailable")

        result = await risk_ranker(_base_state())

    assert result["risk_ranker_done"] is True
    assert len(result["risk_rankings"]) == 3


@pytest.mark.asyncio
async def test_risk_ranker_extends_execution_stages_for_impact():
    state = _base_state()
    state["plan"] = {"subgraphs": ["vulnerabilities", "impact"], "dep_filter": None}

    with patch("src.main_graph.nodes.risk_ranker.create_agent") as mock_factory:
        mock_agent = MagicMock()
        mock_agent.ainvoke = AsyncMock(return_value={"messages": []})
        mock_factory.return_value = mock_agent

        result = await risk_ranker(state)

    # high_risk_deps is non-empty and "impact" is in subgraphs → new stage added
    assert len(result["execution_stages"]) == 1
    stage2 = result["execution_stages"][0]
    assert all(entry["subgraph"] == "impact" for entry in stage2)


@pytest.mark.asyncio
async def test_risk_ranker_no_impact_in_plan_skips_stage_extension():
    state = _base_state()
    state["plan"] = {"subgraphs": ["vulnerabilities"], "dep_filter": None}

    with patch("src.main_graph.nodes.risk_ranker.create_agent") as mock_factory:
        mock_agent = MagicMock()
        mock_agent.ainvoke = AsyncMock(return_value={"messages": []})
        mock_factory.return_value = mock_agent

        result = await risk_ranker(state)

    assert result["execution_stages"] == []


def test_risk_ranker_router_routes_to_execution_planner_when_impact_and_high_risk():
    state = {
        "plan": {"subgraphs": ["impact"]},
        "high_risk_deps": ["express"],
    }
    assert risk_ranker_router(state) == "execution_planner"


def test_risk_ranker_router_routes_to_risk_score_when_no_impact():
    state = {
        "plan": {"subgraphs": ["vulnerabilities"]},
        "high_risk_deps": [],
    }
    assert risk_ranker_router(state) == "risk_score"
