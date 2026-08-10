from __future__ import annotations

import argparse
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scripts.run_subgraph import (
    _run_analysis,
    _run_discovery,
    _run_remediation,
    _run_report,
    main,
)


def _args(**kwargs) -> argparse.Namespace:
    defaults = {
        "job_id": None,
        "concern": "security vulnerabilities",
        "repo": None,
        "prep_result_id": None,
        "analysis_result_id": None,
        "remediate": False,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


@pytest.mark.asyncio
async def test_run_discovery_prints_prep_summary(capsys):
    fake_graph = MagicMock()
    fake_graph.ainvoke = AsyncMock(
        return_value={"prep_result_id": "prep-1", "codegraph_ready": True}
    )
    fake_dao = MagicMock()
    fake_dao.get_prep = AsyncMock(
        return_value=MagicMock(
            detected_package_manager="npm",
            dependency_graph={"direct": {"lodash": {}}},
            project_metadata={"name": "demo"},
        )
    )

    with (
        patch("src.db.result_dao.ResultDAO", return_value=fake_dao),
        patch(
            "src.main_graph.adapters.docker_container_adapter.DockerContainerAdapter"
        ),
        patch(
            "src.main_graph.subgraphs.discovery.graph.build_discovery_subgraph",
            return_value=fake_graph,
        ),
    ):
        await _run_discovery(_args(repo="https://github.com/x/y"))

    fake_graph.ainvoke.assert_awaited_once()
    fake_dao.get_prep.assert_awaited_once_with("prep-1")
    out = capsys.readouterr().out
    assert "prep_result_id = prep-1" in out
    assert "npm" in out


@pytest.mark.asyncio
async def test_run_discovery_reports_error_without_calling_dao():
    fake_graph = MagicMock()
    fake_graph.ainvoke = AsyncMock(return_value={"discovery_error": "clone failed"})
    fake_dao = MagicMock()
    fake_dao.get_prep = AsyncMock()

    with (
        patch("src.db.result_dao.ResultDAO", return_value=fake_dao),
        patch(
            "src.main_graph.adapters.docker_container_adapter.DockerContainerAdapter"
        ),
        patch(
            "src.main_graph.subgraphs.discovery.graph.build_discovery_subgraph",
            return_value=fake_graph,
        ),
    ):
        await _run_discovery(_args(repo="https://github.com/x/y"))

    fake_dao.get_prep.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_analysis_prints_findings(capsys):
    finding = MagicMock(
        severity="high", dep_name="lodash", description="prototype pollution issue"
    )
    fake_dao = MagicMock()
    fake_dao.get_analysis = AsyncMock(
        return_value=MagicMock(findings=[finding], iteration_count=2)
    )
    fake_graph = MagicMock()
    fake_graph.ainvoke = AsyncMock(return_value={"analysis_result_id": "an-1"})

    with (
        patch("src.db.result_dao.ResultDAO", return_value=fake_dao),
        patch(
            "src.main_graph.adapters.docker_container_adapter.DockerContainerAdapter"
        ),
        patch("src.services.job_dao.JobDAO"),
        patch(
            "src.main_graph.subgraphs.analysis.graph.build_analysis_subgraph",
            return_value=fake_graph,
        ),
    ):
        await _run_analysis(_args(prep_result_id="prep-1"))

    fake_dao.get_analysis.assert_awaited_once_with("an-1")
    out = capsys.readouterr().out
    assert "lodash" in out


@pytest.mark.asyncio
async def test_run_report_uses_prep_and_analysis_ids():
    fake_dao = MagicMock()
    fake_dao.get_analysis = AsyncMock()
    fake_dao.get_report = AsyncMock(
        return_value=MagicMock(
            overall_risk_level="high",
            findings=[MagicMock()],
            recommendations=[MagicMock()],
            executive_summary="summary text",
        )
    )
    fake_graph = MagicMock()
    fake_graph.ainvoke = AsyncMock(return_value={"report_result_id": "rep-1"})

    with (
        patch("src.db.result_dao.ResultDAO", return_value=fake_dao),
        patch(
            "src.main_graph.subgraphs.report.graph.build_report_subgraph",
            return_value=fake_graph,
        ),
    ):
        await _run_report(_args(prep_result_id="prep-1", analysis_result_id="an-1"))

    fake_dao.get_analysis.assert_awaited_once_with("an-1")
    fake_dao.get_report.assert_awaited_once_with("rep-1")


@pytest.mark.asyncio
async def test_run_remediation_wires_consent_and_prints_summary(capsys):
    fake_dao = MagicMock()
    fake_dao.get_remediation = AsyncMock(
        return_value=MagicMock(
            consent=True,
            remediations=[
                MagicMock(
                    status="fixed",
                    target_dep="lodash",
                    from_range="^4.17.0",
                    to_range="^4.17.21",
                    pr_url="https://github.com/x/y/pull/1",
                )
            ],
        )
    )
    captured: dict = {}

    async def fake_ainvoke(state, config):
        captured["state"] = state
        captured["config"] = config
        return {"remediation_result_id": "rem-1"}

    fake_graph = MagicMock()
    fake_graph.ainvoke = fake_ainvoke

    with (
        patch("src.db.result_dao.ResultDAO", return_value=fake_dao),
        patch(
            "src.main_graph.adapters.docker_container_adapter.DockerContainerAdapter"
        ),
        patch("src.main_graph.adapters.gh_cli_adapter.GhCliAdapter"),
        patch(
            "src.main_graph.subgraphs.remediation.graph.build_remediation_subgraph",
            return_value=fake_graph,
        ),
    ):
        await _run_remediation(
            _args(prep_result_id="prep-1", analysis_result_id="an-1", remediate=True)
        )

    assert captured["state"] == {
        "job_id": captured["state"]["job_id"],
        "concern": "security vulnerabilities",
        "prep_result_id": "prep-1",
        "analysis_result_id": "an-1",
    }
    assert captured["config"]["configurable"]["remediate"] is True
    fake_dao.get_remediation.assert_awaited_once_with("rem-1")
    out = capsys.readouterr().out
    assert "remediation_result_id = rem-1" in out
    assert "FIXED" in out
    assert "lodash" in out


@pytest.mark.asyncio
async def test_run_remediation_defaults_remediate_false():
    fake_dao = MagicMock()
    fake_dao.get_remediation = AsyncMock(
        return_value=MagicMock(consent=False, remediations=[])
    )
    captured: dict = {}

    async def fake_ainvoke(state, config):
        captured["config"] = config
        return {"remediation_result_id": "rem-2"}

    fake_graph = MagicMock()
    fake_graph.ainvoke = fake_ainvoke

    with (
        patch("src.db.result_dao.ResultDAO", return_value=fake_dao),
        patch(
            "src.main_graph.adapters.docker_container_adapter.DockerContainerAdapter"
        ),
        patch("src.main_graph.adapters.gh_cli_adapter.GhCliAdapter"),
        patch(
            "src.main_graph.subgraphs.remediation.graph.build_remediation_subgraph",
            return_value=fake_graph,
        ),
    ):
        await _run_remediation(
            _args(prep_result_id="prep-1", analysis_result_id="an-1")
        )

    assert captured["config"]["configurable"]["remediate"] is False


def test_main_requires_prep_result_id_for_remediation(monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_subgraph.py",
            "remediation",
            "--concern",
            "x",
            "--analysis-result-id",
            "an-1",
        ],
    )
    with pytest.raises(SystemExit):
        main()


def test_main_requires_analysis_result_id_for_remediation(monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_subgraph.py",
            "remediation",
            "--concern",
            "x",
            "--prep-result-id",
            "prep-1",
        ],
    )
    with pytest.raises(SystemExit):
        main()


def test_main_dispatches_remediation(monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_subgraph.py",
            "remediation",
            "--concern",
            "x",
            "--prep-result-id",
            "prep-1",
            "--analysis-result-id",
            "an-1",
        ],
    )
    mock_run = AsyncMock()

    with patch("scripts.run_subgraph._run_remediation", mock_run):
        main()

    mock_run.assert_awaited_once()
    called_args = mock_run.await_args.args[0]
    assert called_args.prep_result_id == "prep-1"
    assert called_args.analysis_result_id == "an-1"
    assert called_args.remediate is False
