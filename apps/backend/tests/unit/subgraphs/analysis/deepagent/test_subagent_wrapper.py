from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import HumanMessage

from src.main_graph.subgraphs.analysis.deepagent.subagent_wrapper import (
    build_agent_subagent,
)
from src.models.conductor import EvidenceRef, FindingNote
from src.models.results import AgentDispatch, EvidenceBundle, PrepResult


def _make_prep() -> PrepResult:
    return PrepResult(
        job_id="job-1",
        repo_path="/tmp/repo",
        project_metadata={"name": "x"},
        manifest_files=["package.json"],
        detected_package_manager="npm",
        dependency_graph={"direct": {"chalk": "5.0.0"}, "packages": {}},
        discovery_summary="a test repo",
        vector_store_id="",
    )


def _make_bundle() -> EvidenceBundle:
    return EvidenceBundle(
        domain="maintenance",
        hypothesis="chalk may be unmaintained",
        packages_to_focus=["chalk"],
        findings=[
            FindingNote(
                dep_name="chalk",
                severity="low",
                description="stale",
                evidence=[EvidenceRef(tool="npm_outdated", url=None, log_snippet="")],
            )
        ],
        summary="1 finding",
        confidence=0.9,
    )


@pytest.mark.asyncio
async def test_wrapper_extracts_dispatch_runs_agent_and_saves_bundle():
    subagent = build_agent_subagent("maintenance_agent")
    assert subagent["name"] == "maintenance_agent"

    fake_dispatch = AgentDispatch(
        domain="maintenance",
        hypothesis="chalk may be unmaintained",
        packages_to_focus=["chalk"],
        agent_type="maintenance_agent",
    )
    fake_bundle = _make_bundle()
    fake_dao = MagicMock()
    fake_dao.get_prep = AsyncMock(return_value=_make_prep())
    fake_dao.save_bundle = AsyncMock(return_value="bundle-123")

    # get_services is synchronous in production (src/main_graph/config.py:
    # `def get_services(config): return cast(...)`), so it's mocked with a
    # plain MagicMock, not AsyncMock, and we inspect its call_args below to
    # prove the real ambient RunnableConfig reached it (regression test: a
    # prior version of _run hardcoded `{"configurable": {}}` instead of
    # declaring a `config` parameter, which would silently drop this).
    mock_get_services = MagicMock(
        return_value={
            "result_dao": fake_dao,
            "container": MagicMock(),
            "input_cache": None,
        }
    )
    real_config = {"configurable": {"test_marker": "abc123"}}

    with (
        patch(
            "src.main_graph.subgraphs.analysis.deepagent.subagent_wrapper._extract_dispatch",
            new=AsyncMock(return_value=fake_dispatch),
        ),
        patch(
            "src.main_graph.subgraphs.analysis.deepagent.subagent_wrapper.get_services",
            new=mock_get_services,
        ),
        patch(
            "src.main_graph.subgraphs.analysis.agents.maintenance_agent"
            ".MaintenanceAgent.run",
            new=AsyncMock(return_value=(fake_bundle, ["npm_outdated"], 1)),
        ),
    ):
        result = await subagent["runnable"].ainvoke(
            {
                "messages": [HumanMessage(content="check chalk for maintenance risk")],
                "job_id": "job-1",
                "prep_result_id": "prep-1",
                "agent_calls": [],
            },
            real_config,
        )

    assert result["bundle_ids"] == ["bundle-123"]
    assert len(result["agent_calls"]) == 1
    record = result["agent_calls"][0]
    assert record["agent_type"] == "maintenance_agent"
    assert record["bundle_id"] == "bundle-123"
    fake_dao.save_bundle.assert_awaited_once_with(fake_bundle)

    mock_get_services.assert_called_once()
    received_config = mock_get_services.call_args.args[0]
    assert received_config["configurable"].get("test_marker") == "abc123"


@pytest.mark.asyncio
async def test_whole_tree_agent_is_a_noop_if_already_run_this_job():
    subagent = build_agent_subagent("license_agent")
    existing_call = {
        "agent_type": "license_agent",
        "bundle_id": "bundle-existing",
        "conductor_iteration": 0,
        "domain": "license",
        "tools_used": [],
        "react_iterations": 1,
        "started_at": "2026-07-26T00:00:00Z",
        "finished_at": "2026-07-26T00:00:01Z",
    }

    with patch(
        "src.main_graph.subgraphs.analysis.agents.license_agent.LicenseAgent.run"
    ) as mock_run:
        result = await subagent["runnable"].ainvoke(
            {
                "messages": [HumanMessage(content="check licenses")],
                "job_id": "job-1",
                "prep_result_id": "prep-1",
                "agent_calls": [existing_call],
            },
            {"configurable": {}},
        )
        mock_run.assert_not_called()

    assert result["bundle_ids"] == ["bundle-existing"]
    # The node itself returns an empty agent_calls delta (no duplicate
    # record), but ainvoke on this single-node graph seeds the
    # Annotated[list, operator.add] channel directly from the input state,
    # so the pre-existing call is still present in the final merged state.
    # What matters for the no-op behavior is that it's unchanged (not
    # doubled) and that agent_class().run() was never invoked.
    assert result["agent_calls"] == [existing_call]
