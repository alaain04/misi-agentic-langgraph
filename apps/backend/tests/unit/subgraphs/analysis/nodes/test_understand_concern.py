from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.main_graph.subgraphs.analysis.concern import Concern
from src.main_graph.subgraphs.analysis.nodes.understand_concern import (
    understand_concern,
)
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
async def test_understand_concern_writes_structured_concern_to_state():
    fake_concern = Concern(
        is_valid=True,
        type=["vulnerability"],
        scope="all_dependencies",
        packages=[],
        requires_per_dependency_analysis=False,
        preferred_agents=["vulnerability_agent"],
    )
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        return_value=fake_concern
    )
    fake_dao = MagicMock()
    fake_dao.get_prep = AsyncMock(return_value=_make_prep())
    mock_get_services = MagicMock(return_value={"result_dao": fake_dao})

    with (
        patch(
            "src.main_graph.subgraphs.analysis.nodes.understand_concern._llm",
            mock_llm,
        ),
        patch(
            "src.main_graph.subgraphs.analysis.nodes.understand_concern.get_services",
            mock_get_services,
        ),
    ):
        result = await understand_concern(
            {
                "job_id": "job-1",
                "concern": "check for known CVEs",
                "prep_result_id": "prep-1",
            },
            {"configurable": {}},
        )

    assert result["structured_concern"] == fake_concern.model_dump()
    mock_llm.with_structured_output.assert_called_once_with(
        Concern, method="function_calling"
    )


@pytest.mark.asyncio
async def test_understand_concern_passes_direct_deps_and_roster_as_context():
    fake_concern = Concern(
        is_valid=True,
        type=["license"],
        scope="all_dependencies",
        packages=[],
        requires_per_dependency_analysis=False,
        preferred_agents=["license_agent"],
    )
    captured: dict = {}

    async def _ainvoke(messages):
        captured["messages"] = messages
        return fake_concern

    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        side_effect=_ainvoke
    )
    fake_dao = MagicMock()
    fake_dao.get_prep = AsyncMock(return_value=_make_prep())
    mock_get_services = MagicMock(return_value={"result_dao": fake_dao})

    with (
        patch(
            "src.main_graph.subgraphs.analysis.nodes.understand_concern._llm",
            mock_llm,
        ),
        patch(
            "src.main_graph.subgraphs.analysis.nodes.understand_concern.get_services",
            mock_get_services,
        ),
    ):
        await understand_concern(
            {
                "job_id": "job-1",
                "concern": "check licenses",
                "prep_result_id": "prep-1",
            },
            {"configurable": {}},
        )

    system_content = captured["messages"][0]["content"]
    assert "lodash@4.17.20" in system_content
    assert "vulnerability_agent" in system_content
    assert captured["messages"][1]["content"] == "check licenses"
