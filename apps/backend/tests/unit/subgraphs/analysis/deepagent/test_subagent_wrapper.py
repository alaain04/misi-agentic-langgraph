from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

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


@pytest.mark.asyncio
async def test_budget_exhausted_skips_dispatch_and_returns_a_message():
    subagent = build_agent_subagent("maintenance_agent")
    already_used_calls = [
        {"agent_type": "maintenance_agent", "bundle_id": f"b{i}"} for i in range(8)
    ]

    with patch(
        "src.main_graph.subgraphs.analysis.agents.maintenance_agent"
        ".MaintenanceAgent.run"
    ) as mock_run:
        result = await subagent["runnable"].ainvoke(
            {
                "messages": [HumanMessage(content="check chalk")],
                "job_id": "job-1",
                "prep_result_id": "prep-1",
                "agent_calls": already_used_calls,
            },
            {"configurable": {}},
        )
        mock_run.assert_not_called()

    assert result["bundle_ids"] == []
    # The node returns an empty agent_calls delta (no new record), but --
    # same reducer mechanism documented on
    # test_whole_tree_agent_is_a_noop_if_already_run_this_job above --
    # Annotated[list, operator.add] seeds the channel from the input
    # state, so the 8 pre-existing calls that triggered the budget check are
    # still present in the merged result. What matters is that no 9th call
    # was appended.
    assert result["agent_calls"] == already_used_calls
    assert len(result["messages"]) == 1
    assert isinstance(result["messages"][0], AIMessage)
    assert "budget" in result["messages"][0].content.lower()


@pytest.mark.asyncio
async def test_semaphore_caps_concurrent_specialist_calls():
    subagent = build_agent_subagent("maintenance_agent")
    fake_dao = MagicMock()
    fake_dao.get_prep = AsyncMock(return_value=_make_prep())
    fake_dao.save_bundle = AsyncMock(return_value="bundle-1")
    mock_get_services = MagicMock(
        return_value={
            "result_dao": fake_dao,
            "container": MagicMock(),
            "input_cache": None,
        }
    )

    concurrent = 0
    peak = 0

    async def _slow_run(*args, **kwargs):
        nonlocal concurrent, peak
        concurrent += 1
        peak = max(peak, concurrent)
        await asyncio.sleep(0.02)
        concurrent -= 1
        return _make_bundle(), ["npm_outdated"], 1

    test_semaphore = asyncio.Semaphore(2)

    with (
        patch(
            "src.main_graph.subgraphs.analysis.deepagent.subagent_wrapper._extract_dispatch",
            new=AsyncMock(
                return_value=AgentDispatch(
                    domain="maintenance",
                    hypothesis="check chalk",
                    packages_to_focus=["chalk"],
                    agent_type="maintenance_agent",
                )
            ),
        ),
        patch(
            "src.main_graph.subgraphs.analysis.deepagent.subagent_wrapper.get_services",
            new=mock_get_services,
        ),
        patch(
            "src.main_graph.subgraphs.analysis.deepagent.subagent_wrapper.SPECIALIST_SEMAPHORE",
            test_semaphore,
        ),
        patch(
            "src.main_graph.subgraphs.analysis.agents.maintenance_agent"
            ".MaintenanceAgent.run",
            new=_slow_run,
        ),
    ):
        await asyncio.gather(
            *[
                subagent["runnable"].ainvoke(
                    {
                        "messages": [HumanMessage(content="check chalk")],
                        "job_id": "job-1",
                        "prep_result_id": "prep-1",
                        "agent_calls": [],
                    },
                    {"configurable": {}},
                )
                for _ in range(5)
            ]
        )

    assert peak <= 2
