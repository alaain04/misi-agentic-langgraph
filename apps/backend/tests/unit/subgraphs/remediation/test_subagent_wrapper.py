from __future__ import annotations

import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.main_graph.subgraphs.remediation.deepagent.subagent_wrapper import (
    build_target_subagent,
)
from src.models.remediation import RemediationOutcome
from src.models.results import PrepResult


def _cloned_repo() -> tuple[str, str]:
    """A real dst/repo-shaped temp dir mirroring copy_repo's actual
    contract (see test_replay.py's mkdtemp_root/repo pattern). _run now
    cleans up via shutil.rmtree(os.path.dirname(work_dir)) in a finally
    block, so a mocked copy_repo returning anything not shaped this way
    (e.g. a bare tmp_path, or "/tmp/fake-clone" whose dirname is "/tmp")
    would make that cleanup target something dangerously broad instead of
    the disposable clone directory it's meant to remove."""
    mkdtemp_root = tempfile.mkdtemp(prefix="test-remediation-")
    work_dir = os.path.join(mkdtemp_root, "repo")
    os.makedirs(work_dir)
    return mkdtemp_root, work_dir


def _prep(**overrides):
    defaults = dict(
        id="prep-1",
        job_id="job-1",
        repo_path="/tmp/repo",
        project_metadata={"name": "x"},
        manifest_files=["package.json"],
        docker_image="node:lts-alpine",
        detected_package_manager="npm",
        dependency_graph={"direct": {"eslint": "8.0.0"}, "packages": {}},
        discovery_summary="a test repo",
        vector_store_id="",
    )
    defaults.update(overrides)
    return PrepResult(**defaults)


class _FakeHumanMessage:
    def __init__(self, content):
        self.content = content


@pytest.mark.asyncio
async def test_run_resolves_known_target_and_reports_outcome(tmp_path):
    spec = build_target_subagent()
    assert spec["name"] == "remediate_target"

    prep = _prep(repo_path=str(tmp_path))
    (tmp_path / "package.json").write_text('{"dependencies": {"eslint": "8.0.0"}}')

    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=prep)
    container = MagicMock()
    config = {"configurable": {"result_dao": dao, "container": container}}

    mkdtemp_root, work_dir = _cloned_repo()

    with (
        patch(
            "src.main_graph.subgraphs.remediation.deepagent.subagent_wrapper._extract_target_dep",
            AsyncMock(return_value="eslint"),
        ),
        patch(
            "src.main_graph.subgraphs.remediation.deepagent.subagent_wrapper.copy_repo",
            return_value=work_dir,
        ),
        patch(
            "src.main_graph.subgraphs.remediation.deepagent.subagent_wrapper.create_deep_agent"
        ) as mock_create,
    ):
        nested_agent = AsyncMock()
        nested_agent.ainvoke = AsyncMock(
            return_value={
                "structured_response": RemediationOutcome(
                    strategy="bump", to_range="^9.0.0", summary="clean bump"
                )
            }
        )
        mock_create.return_value = nested_agent

        result = await spec["runnable"].ainvoke(
            {
                "messages": [{"role": "user", "content": "Remediate eslint."}],
                "job_id": "job-1",
                "prep_result_id": "prep-1",
                "evidence": {},
                "targets": {
                    "eslint": {
                        "target_dep": "eslint",
                        "addresses": ["eslint"],
                        "current_range": "8.0.0",
                    }
                },
                "remediations": {},
                "requires_edges": {},
            },
            config,
        )

    assert result["remediations"]["eslint"]["to_range"] == "^9.0.0"
    assert (
        result["remediations"]["eslint"]["status"] == "skipped"
    )  # provisional, gate sets the real value
    assert result["requires_edges"] == {}
    # _run must clean up the clone it made, targeting the mkdtemp root
    # copy_repo actually created (not the caller's original repo_path).
    assert not os.path.exists(mkdtemp_root)


@pytest.mark.asyncio
async def test_run_records_requires_edge():
    prep = _prep()
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=prep)
    config = {"configurable": {"result_dao": dao, "container": MagicMock()}}

    mkdtemp_root, work_dir = _cloned_repo()

    spec = build_target_subagent()
    with (
        patch(
            "src.main_graph.subgraphs.remediation.deepagent.subagent_wrapper._extract_target_dep",
            AsyncMock(return_value="eslint"),
        ),
        patch(
            "src.main_graph.subgraphs.remediation.deepagent.subagent_wrapper.copy_repo",
            return_value=work_dir,
        ),
        patch(
            "src.main_graph.subgraphs.remediation.deepagent.subagent_wrapper.create_deep_agent"
        ) as mock_create,
    ):
        nested_agent = AsyncMock()
        nested_agent.ainvoke = AsyncMock(
            return_value={
                "structured_response": RemediationOutcome(
                    strategy="bump_with_codemod",
                    to_range="^9.0.0",
                    requires=["eslint-plugin-react"],
                    summary="bumped and adapted call sites; plugin needs a bump too",
                )
            }
        )
        mock_create.return_value = nested_agent

        result = await spec["runnable"].ainvoke(
            {
                "messages": [{"role": "user", "content": "Remediate eslint."}],
                "job_id": "job-1",
                "prep_result_id": "prep-1",
                "evidence": {},
                "targets": {
                    "eslint": {
                        "target_dep": "eslint",
                        "addresses": ["eslint"],
                        "current_range": "8.0.0",
                    }
                },
                "remediations": {},
                "requires_edges": {},
            },
            config,
        )

    assert result["requires_edges"]["eslint"] == ["eslint-plugin-react"]
    assert not os.path.exists(mkdtemp_root)


@pytest.mark.asyncio
async def test_run_synthesizes_target_for_unknown_dep_name():
    prep = _prep(
        dependency_graph={"direct": {"eslint-plugin-react": "^7.0.0"}, "packages": {}}
    )
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=prep)
    config = {"configurable": {"result_dao": dao, "container": MagicMock()}}

    mkdtemp_root, work_dir = _cloned_repo()

    spec = build_target_subagent()
    with (
        patch(
            "src.main_graph.subgraphs.remediation.deepagent.subagent_wrapper._extract_target_dep",
            AsyncMock(return_value="eslint-plugin-react"),
        ),
        patch(
            "src.main_graph.subgraphs.remediation.deepagent.subagent_wrapper.copy_repo",
            return_value=work_dir,
        ),
        patch(
            "src.main_graph.subgraphs.remediation.deepagent.subagent_wrapper.create_deep_agent"
        ) as mock_create,
    ):
        nested_agent = AsyncMock()
        nested_agent.ainvoke = AsyncMock(
            return_value={
                "structured_response": RemediationOutcome(
                    strategy="bump", to_range="^8.0.0"
                )
            }
        )
        mock_create.return_value = nested_agent

        # note: "targets" does NOT contain eslint-plugin-react - it must be
        # synthesized from the dependency graph's direct-range lookup
        result = await spec["runnable"].ainvoke(
            {
                "messages": [
                    {"role": "user", "content": "Remediate eslint-plugin-react."}
                ],
                "job_id": "job-1",
                "prep_result_id": "prep-1",
                "evidence": {},
                "targets": {},
                "remediations": {},
                "requires_edges": {},
            },
            config,
        )

    remediation = result["remediations"]["eslint-plugin-react"]
    assert remediation["from_range"] == "^7.0.0"
    assert remediation["addresses"] == []
    assert not os.path.exists(mkdtemp_root)


@pytest.mark.asyncio
async def test_run_reports_failed_when_agent_produces_no_structured_response():
    prep = _prep()
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=prep)
    config = {"configurable": {"result_dao": dao, "container": MagicMock()}}

    mkdtemp_root, work_dir = _cloned_repo()

    spec = build_target_subagent()
    with (
        patch(
            "src.main_graph.subgraphs.remediation.deepagent.subagent_wrapper._extract_target_dep",
            AsyncMock(return_value="eslint"),
        ),
        patch(
            "src.main_graph.subgraphs.remediation.deepagent.subagent_wrapper.copy_repo",
            return_value=work_dir,
        ),
        patch(
            "src.main_graph.subgraphs.remediation.deepagent.subagent_wrapper.create_deep_agent"
        ) as mock_create,
    ):
        nested_agent = AsyncMock()
        nested_agent.ainvoke = AsyncMock(return_value={})
        mock_create.return_value = nested_agent

        result = await spec["runnable"].ainvoke(
            {
                "messages": [{"role": "user", "content": "Remediate eslint."}],
                "job_id": "job-1",
                "prep_result_id": "prep-1",
                "evidence": {},
                "targets": {
                    "eslint": {
                        "target_dep": "eslint",
                        "addresses": [],
                        "current_range": "8.0.0",
                    }
                },
                "remediations": {},
                "requires_edges": {},
            },
            config,
        )

    assert result["remediations"]["eslint"]["status"] == "failed"
    assert (
        result["remediations"]["eslint"]["skip_reason"]
        == "agent produced no structured decision"
    )
    assert not os.path.exists(mkdtemp_root)


@pytest.mark.asyncio
async def test_run_reports_failed_when_structured_response_fails_validation():
    """A present-but-malformed structured_response (e.g. real deepagents
    machinery returning a dict with a field of the wrong shape/type) must
    degrade the same way an absent one does, not crash the node with an
    uncaught pydantic.ValidationError."""
    prep = _prep()
    dao = AsyncMock()
    dao.get_prep = AsyncMock(return_value=prep)
    config = {"configurable": {"result_dao": dao, "container": MagicMock()}}

    mkdtemp_root, work_dir = _cloned_repo()

    spec = build_target_subagent()
    with (
        patch(
            "src.main_graph.subgraphs.remediation.deepagent.subagent_wrapper._extract_target_dep",
            AsyncMock(return_value="eslint"),
        ),
        patch(
            "src.main_graph.subgraphs.remediation.deepagent.subagent_wrapper.copy_repo",
            return_value=work_dir,
        ),
        patch(
            "src.main_graph.subgraphs.remediation.deepagent.subagent_wrapper.create_deep_agent"
        ) as mock_create,
    ):
        nested_agent = AsyncMock()
        # strategy has an invalid Literal value and requires is the wrong
        # type entirely -- guaranteed to fail RemediationOutcome validation
        # without being None or an instance already.
        nested_agent.ainvoke = AsyncMock(
            return_value={
                "structured_response": {
                    "strategy": "not-a-real-strategy",
                    "requires": "not-a-list",
                }
            }
        )
        mock_create.return_value = nested_agent

        result = await spec["runnable"].ainvoke(
            {
                "messages": [{"role": "user", "content": "Remediate eslint."}],
                "job_id": "job-1",
                "prep_result_id": "prep-1",
                "evidence": {},
                "targets": {
                    "eslint": {
                        "target_dep": "eslint",
                        "addresses": [],
                        "current_range": "8.0.0",
                    }
                },
                "remediations": {},
                "requires_edges": {},
            },
            config,
        )

    assert result["remediations"]["eslint"]["status"] == "failed"
    assert (
        result["remediations"]["eslint"]["skip_reason"]
        == "agent produced no structured decision"
    )
    assert not os.path.exists(mkdtemp_root)
