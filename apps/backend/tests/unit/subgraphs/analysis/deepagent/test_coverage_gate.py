from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.main_graph.subgraphs.analysis.deepagent.nodes import coverage_gate
from src.models.results import PrepResult


def _make_prep() -> PrepResult:
    return PrepResult(
        job_id="job-1",
        repo_path="/tmp/repo",
        project_metadata={"name": "x"},
        manifest_files=["package.json"],
        detected_package_manager="npm",
        dependency_graph={"direct": {"lodash": "4.17.20"}, "packages": {}},
    )


def _structured_concern(**overrides) -> dict:
    defaults = dict(
        is_valid=True,
        type=["maintenance"],
        scope="all_dependencies",
        packages=[],
        requires_per_dependency_analysis=False,
        preferred_agents=["maintenance_agent"],
    )
    defaults.update(overrides)
    return defaults


@pytest.mark.asyncio
async def test_short_circuits_when_per_dependency_analysis_not_required():
    fake_dao = MagicMock()
    fake_dao.get_prep = AsyncMock(return_value=_make_prep())
    fake_dao.get_bundles = AsyncMock(side_effect=AssertionError("must not be called"))
    mock_get_services = MagicMock(return_value={"result_dao": fake_dao})

    with (
        patch(
            "src.main_graph.subgraphs.analysis.deepagent.nodes.get_services",
            mock_get_services,
        ),
        patch(
            "src.main_graph.subgraphs.analysis.deepagent.nodes"
            ".whole_tree_scan_satisfies_concern",
            AsyncMock(side_effect=AssertionError("must not be called")),
        ),
    ):
        result = await coverage_gate(
            {
                "job_id": "job-1",
                "concern": "is lodash maintained?",
                "prep_result_id": "prep-1",
                "structured_concern": _structured_concern(
                    requires_per_dependency_analysis=False
                ),
                "agent_calls": [],
            },
            {"configurable": {}},
        )

    assert result["missing_deps"] == []
    assert result["correction_rounds"] == 1


@pytest.mark.asyncio
async def test_still_enforces_when_per_dependency_analysis_required():
    fake_dao = MagicMock()
    fake_dao.get_prep = AsyncMock(return_value=_make_prep())
    fake_dao.get_bundles = AsyncMock(return_value=[])
    mock_get_services = MagicMock(return_value={"result_dao": fake_dao})

    with patch(
        "src.main_graph.subgraphs.analysis.deepagent.nodes.get_services",
        mock_get_services,
    ):
        result = await coverage_gate(
            {
                "job_id": "job-1",
                "concern": "check every direct dependency for maintenance risk",
                "prep_result_id": "prep-1",
                "structured_concern": _structured_concern(
                    requires_per_dependency_analysis=True
                ),
                "agent_calls": [],
            },
            {"configurable": {}},
        )

    assert result["missing_deps"] == ["lodash"]


@pytest.mark.asyncio
async def test_defaults_to_enforcing_when_structured_concern_is_missing():
    fake_dao = MagicMock()
    fake_dao.get_prep = AsyncMock(return_value=_make_prep())
    fake_dao.get_bundles = AsyncMock(return_value=[])
    mock_get_services = MagicMock(return_value={"result_dao": fake_dao})

    with patch(
        "src.main_graph.subgraphs.analysis.deepagent.nodes.get_services",
        mock_get_services,
    ):
        result = await coverage_gate(
            {
                "job_id": "job-1",
                "concern": "check maintenance",
                "prep_result_id": "prep-1",
                "agent_calls": [],
            },
            {"configurable": {}},
        )

    assert result["missing_deps"] == ["lodash"]
