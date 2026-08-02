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


@pytest.mark.asyncio
async def test_first_round_excludes_already_run_whole_tree_agents_from_roster():
    """A mixed concern (vulnerability + maintenance) reaches the deep agent
    only for the maintenance remainder -- run_direct_agents (the router's
    mandatory whole-tree prefix) already ran vulnerability_agent and recorded
    it in the outer state's agent_calls before this node ever runs. The
    prompt must exclude vulnerability_agent from the available-specialists
    roster and tell the deep agent it's already done, and the deep agent's
    own internal state must be seeded with that prior work (not started
    empty) so the D8 dedup safety net in subagent_wrapper recognizes it even
    if the deep agent's LLM ignores the prompt and tries to dispatch it
    again."""
    fake_dao = MagicMock()
    fake_dao.get_prep = AsyncMock(return_value=_make_prep())
    mock_get_services = MagicMock(return_value={"result_dao": fake_dao})

    captured = {}

    async def _fake_ainvoke(deepagent_state, run_config):
        captured["system_content"] = deepagent_state["messages"][0].content
        captured["seeded_bundle_ids"] = deepagent_state["bundle_ids"]
        captured["seeded_agent_calls"] = deepagent_state["agent_calls"]
        return {"bundle_ids": [], "agent_calls": []}

    fake_deep_agent = MagicMock()
    fake_deep_agent.ainvoke = AsyncMock(side_effect=_fake_ainvoke)

    existing_vuln_call = {
        "agent_type": "vulnerability_agent",
        "bundle_id": "bundle-vuln",
        "conductor_iteration": 0,
        "domain": "vulnerability",
        "packages_to_focus": [],
        "tools_used": ["trivy"],
        "react_iterations": 1,
        "started_at": "2026-08-02T00:00:00Z",
        "finished_at": "2026-08-02T00:00:01Z",
    }

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
                "concern": "vulnerabilities and unmaintained dependencies",
                "prep_result_id": "prep-1",
                "structured_concern": {
                    "type": ["vulnerability", "maintenance"],
                    "scope": "all_dependencies",
                    "packages": [],
                    "requires_per_dependency_analysis": False,
                    "preferred_agents": ["vulnerability_agent", "maintenance_agent"],
                },
                "bundle_ids": ["bundle-vuln"],
                "agent_calls": [existing_vuln_call],
            },
            {"configurable": {}},
        )

    content = captured["system_content"]
    assert "- vulnerability_agent:" not in content
    assert "- maintenance_agent:" in content
    assert "Already completed for this concern: ['vulnerability_agent']" in content
    assert "do not dispatch these again" in content

    # The deep agent's own internal tracking must be seeded with the prefix's
    # work, not started empty -- this is what makes the D8 dedup in
    # subagent_wrapper recognize it as already-run.
    assert captured["seeded_bundle_ids"] == ["bundle-vuln"]
    assert captured["seeded_agent_calls"] == [existing_vuln_call]
