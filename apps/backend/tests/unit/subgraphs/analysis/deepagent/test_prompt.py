from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.main_graph.subgraphs.analysis.deepagent import nodes as deepagent_nodes
from src.main_graph.subgraphs.analysis.deepagent.limits import DEEPAGENT_LIMITS
from src.models.results import PrepResult


def _make_prep() -> PrepResult:
    return PrepResult(
        job_id="job-1",
        repo_path="/tmp/repo",
        project_metadata={"name": "x"},
        manifest_files=["package.json"],
        detected_package_manager="npm",
        dependency_graph={"direct": {"lodash": "4.17.20"}, "packages": {}},
        discovery_summary="a test repo",
        vector_store_id="",
    )


@pytest.mark.asyncio
async def test_first_round_system_message_includes_budget_and_structured_concern():
    fake_dao = MagicMock()
    fake_dao.get_prep = AsyncMock(return_value=_make_prep())
    mock_get_services = MagicMock(return_value={"result_dao": fake_dao})

    captured = {}

    async def _fake_ainvoke(deepagent_state, run_config):
        captured["system_content"] = deepagent_state["messages"][0].content
        return {"bundle_ids": [], "agent_calls": []}

    fake_deep_agent = MagicMock()
    fake_deep_agent.ainvoke = AsyncMock(side_effect=_fake_ainvoke)

    with (
        patch(
            "src.main_graph.subgraphs.analysis.deepagent.nodes.get_services",
            mock_get_services,
        ),
        patch.object(deepagent_nodes, "_deep_agent", fake_deep_agent),
    ):
        await deepagent_nodes.analysis_deepagent_node(
            {
                "job_id": "job-1",
                "concern": "check whether lodash is maintained",
                "prep_result_id": "prep-1",
                "structured_concern": {
                    "type": ["maintenance"],
                    "scope": "all_dependencies",
                    "packages": [],
                    "requires_per_dependency_analysis": True,
                    "preferred_agents": ["maintenance_agent"],
                },
            },
            {"configurable": {}},
        )

    content = captured["system_content"]
    assert str(DEEPAGENT_LIMITS.max_specialist_calls) in content
    assert str(DEEPAGENT_LIMITS.max_parallel_calls) in content
    assert "type=['maintenance']" in content
    assert "scope=all_dependencies" in content
    assert "Prefer the smallest plan that completely answers the concern" in content
    assert "prioritize the" in content.lower()
