from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.main_graph.subgraphs.analysis.concern import Concern, ConcernDraft
from src.main_graph.subgraphs.analysis.nodes.understand_concern import (
    understand_concern,
)
from src.models.results import PrepResult


def _make_prep(direct: dict[str, str] | None = None) -> PrepResult:
    return PrepResult(
        job_id="job-1",
        repo_path="/tmp/repo",
        project_metadata={"name": "x"},
        manifest_files=["package.json"],
        detected_package_manager="npm",
        dependency_graph={"direct": direct or {"lodash": "4.17.20"}, "packages": {}},
        vector_store_id="",
    )


def _mock_llm(draft: ConcernDraft) -> MagicMock:
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(return_value=draft)
    return mock_llm


def _mock_services(prep: PrepResult) -> MagicMock:
    fake_dao = MagicMock()
    fake_dao.get_prep = AsyncMock(return_value=prep)
    return MagicMock(return_value={"result_dao": fake_dao})


async def _run(
    draft: ConcernDraft, prep: PrepResult, concern_text: str = "check for known CVEs"
) -> dict:
    with (
        patch(
            "src.main_graph.subgraphs.analysis.nodes.understand_concern._llm",
            _mock_llm(draft),
        ),
        patch(
            "src.main_graph.subgraphs.analysis.nodes.understand_concern.get_services",
            _mock_services(prep),
        ),
    ):
        return await understand_concern(
            {"job_id": "job-1", "concern": concern_text, "prep_result_id": "prep-1"},
            {"configurable": {}},
        )


@pytest.mark.asyncio
async def test_derives_scope_and_preferred_agents_for_whole_tree_concern():
    draft = ConcernDraft(
        is_valid=True,
        type=["vulnerability"],
        packages=[],
        requires_per_dependency_analysis=False,
    )
    result = await _run(draft, _make_prep())
    concern = Concern(**result["structured_concern"])
    assert concern.is_valid is True
    assert concern.scope == "all_dependencies"
    assert concern.preferred_agents == ["vulnerability_agent"]


@pytest.mark.asyncio
async def test_derives_specific_packages_scope_when_packages_named():
    draft = ConcernDraft(
        is_valid=True,
        type=["vulnerability"],
        packages=["lodash"],
        requires_per_dependency_analysis=False,
    )
    result = await _run(draft, _make_prep({"lodash": "4.17.20"}))
    concern = Concern(**result["structured_concern"])
    assert concern.scope == "specific_packages"
    assert concern.packages == ["lodash"]


@pytest.mark.asyncio
async def test_multi_type_preferred_agents_matches_agents_for_types_order():
    draft = ConcernDraft(
        is_valid=True,
        type=["license", "vulnerability"],
        packages=[],
        requires_per_dependency_analysis=False,
    )
    result = await _run(draft, _make_prep())
    concern = Concern(**result["structured_concern"])
    assert concern.preferred_agents == ["vulnerability_agent", "license_agent"]


@pytest.mark.asyncio
async def test_unknown_package_forces_invalid_concern():
    draft = ConcernDraft(
        is_valid=True,
        type=["vulnerability"],
        packages=["left-pad"],
        requires_per_dependency_analysis=False,
    )
    result = await _run(draft, _make_prep({"lodash": "4.17.20"}))
    concern = Concern(**result["structured_concern"])
    assert concern.is_valid is False
    assert concern.type == ["other"]
    assert concern.preferred_agents == []


@pytest.mark.asyncio
async def test_llm_invalid_draft_stays_invalid_regardless_of_packages():
    draft = ConcernDraft(
        is_valid=False,
        type=["other"],
        packages=[],
        requires_per_dependency_analysis=False,
    )
    result = await _run(draft, _make_prep(), concern_text="hello there")
    concern = Concern(**result["structured_concern"])
    assert concern.is_valid is False


@pytest.mark.asyncio
async def test_passes_direct_deps_and_roster_as_context():
    draft = ConcernDraft(
        is_valid=True,
        type=["license"],
        packages=[],
        requires_per_dependency_analysis=False,
    )
    captured: dict = {}

    async def _ainvoke(messages):
        captured["messages"] = messages
        return draft

    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        side_effect=_ainvoke
    )

    with (
        patch(
            "src.main_graph.subgraphs.analysis.nodes.understand_concern._llm",
            mock_llm,
        ),
        patch(
            "src.main_graph.subgraphs.analysis.nodes.understand_concern.get_services",
            _mock_services(_make_prep()),
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
    mock_llm.with_structured_output.assert_called_once_with(
        ConcernDraft, method="function_calling"
    )
