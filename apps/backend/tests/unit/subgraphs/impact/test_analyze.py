from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.main_graph.subgraphs.ingestion_subgraphs.impact.models import ImpactEntry
from src.main_graph.subgraphs.ingestion_subgraphs.impact.nodes.analyze import analyze


@pytest.fixture
def _base_state():
    return {
        "dependency_name": "express",
        "repo_path": "/tmp/test-repo",
        "sbom_cyclonedx": {
            "components": [{"name": "express", "version": "4.18.2"}],
            "dependencies": [
                {"ref": "myapp@1.0.0", "dependsOn": ["express@4.18.2"]},
                {"ref": "express@4.18.2", "dependsOn": []},
            ],
        },
        "discovery_summary": "",
        "concern": "security",
    }


@pytest.mark.asyncio
async def test_analyze_calls_agent_and_saves_result(_base_state):
    mock_entry = ImpactEntry(
        dep_name="express",
        usage_count=3,
        affected_files=["src/app.ts"],
        api_surface_used=["Router"],
        usage_summary="Used as HTTP framework",
        direct_dependents=1,
        transitive_dependents=1,
        max_depth=1,
        blast_radius_summary="Affects 1 package",
    )

    with (
        patch(
            "src.main_graph.subgraphs.ingestion_subgraphs.impact.nodes.analyze.create_agent"
        ) as mock_factory,
        patch(
            "src.main_graph.subgraphs.ingestion_subgraphs.impact.nodes.analyze.impact_dao"
        ) as mock_dao,
    ):
        mock_agent = MagicMock()
        mock_agent.ainvoke = AsyncMock(return_value={"structured_response": mock_entry})
        mock_factory.return_value = mock_agent
        mock_dao.save = AsyncMock(return_value="abc123")

        result = await analyze(_base_state)

    assert result["result_id"] == "abc123"
    mock_dao.save.assert_called_once()
    saved = mock_dao.save.call_args[0][0]
    assert saved.dep_name == "express"
    assert saved.usage_count == 3


@pytest.mark.asyncio
async def test_analyze_saves_empty_entry_when_no_dep_name():
    with patch(
        "src.main_graph.subgraphs.ingestion_subgraphs.impact.nodes.analyze.impact_dao"
    ) as mock_dao:
        mock_dao.save = AsyncMock(return_value="empty123")
        result = await analyze(
            {
                "dependency_name": "",
                "repo_path": "/tmp/repo",
                "sbom_cyclonedx": {},
                "discovery_summary": "",
                "concern": "",
            }
        )
    assert result["result_id"] == "empty123"


@pytest.mark.asyncio
async def test_analyze_saves_empty_entry_when_no_repo_path():
    with patch(
        "src.main_graph.subgraphs.ingestion_subgraphs.impact.nodes.analyze.impact_dao"
    ) as mock_dao:
        mock_dao.save = AsyncMock(return_value="empty456")
        result = await analyze(
            {
                "dependency_name": "express",
                "repo_path": "",
                "sbom_cyclonedx": {},
                "discovery_summary": "",
                "concern": "",
            }
        )
    assert result["result_id"] == "empty456"


@pytest.mark.asyncio
async def test_analyze_falls_back_to_empty_entry_on_agent_exception(_base_state):
    with (
        patch(
            "src.main_graph.subgraphs.ingestion_subgraphs.impact.nodes.analyze.create_agent"
        ) as mock_factory,
        patch(
            "src.main_graph.subgraphs.ingestion_subgraphs.impact.nodes.analyze.impact_dao"
        ) as mock_dao,
    ):
        mock_factory.side_effect = RuntimeError("LLM unavailable")
        mock_dao.save = AsyncMock(return_value="fallback789")
        result = await analyze(_base_state)

    assert result["result_id"] == "fallback789"
