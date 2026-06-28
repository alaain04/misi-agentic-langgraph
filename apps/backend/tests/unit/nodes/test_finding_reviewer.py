from unittest.mock import AsyncMock, patch

from src.main_graph.constants import FINDING_REVIEWER
from src.main_graph.nodes.finding_reviewer import _above_threshold, _check_criteria, finding_reviewer
from src.models.job import Job, JobMetadata
from src.models.risk_finding import RiskFinding


def _make_finding(dep, score, confidence, severity, evidence_count=2):
    evs = [f"ev{i}" for i in range(evidence_count)]
    return RiskFinding(
        dep_name=dep, risk_score=score, confidence=confidence,
        severity=severity, hypotheses=[], supporting_evidence=evs,
        contradictions=[], missing_evidence=[], summary="test summary",
        recommendation="update package", alternatives=["safer-alt"],
    )


async def test_criteria_pass_when_all_met():
    findings = [_make_finding("lodash", 8.0, 0.8, "high", evidence_count=3)]
    result = await _check_criteria(findings, [])
    assert result["approved"] is True
    assert result["failed_criteria"] == []


async def test_criteria_fail_high_score_low_confidence():
    findings = [_make_finding("lodash", 8.5, 0.3, "high")]
    result = await _check_criteria(findings, [])
    assert result["approved"] is False
    assert any("confidence" in c.lower() for c in result["failed_criteria"])


async def test_criteria_fail_high_sev_no_alternative():
    f = _make_finding("lodash", 8.0, 0.8, "high")
    f.alternatives = []
    f.recommendation = None
    result = await _check_criteria([f], [])
    assert result["approved"] is False


async def test_finding_reviewer_stores_messages_for_any_findings():
    dao = AsyncMock()
    dao.get.return_value = None  # fresh run — no stored artifact
    config = {"configurable": {"job_repo": dao}}
    state = {
        "job_id": "job-1",
        "risk_findings": [_make_finding("lodash", 8.0, 0.8, "high", evidence_count=3)],
        "evidence": [],
        "review_iterations": 0,
    }

    with patch("src.main_graph.nodes.finding_reviewer.interrupt", return_value="acknowledged"):
        result = await finding_reviewer(state, config)

    assert result["review_approved"] is True

    calls = dao.push_artifact_message.await_args_list
    assert len(calls) == 2

    assert calls[0].args[0] == "job-1"
    assert calls[0].args[1] == FINDING_REVIEWER
    assert calls[0].args[2]["role"] == "assistant"
    assert "content" in calls[0].args[2]

    assert calls[1].args[1] == FINDING_REVIEWER
    assert calls[1].args[2]["role"] == "human"
    assert calls[1].args[2]["content"] == "acknowledged"
    assert calls[1].args[2]["action"] == "approve"

    dao.update_artifact_data.assert_awaited_once()
    data_call = dao.update_artifact_data.await_args_list[0]
    assert data_call.args[1] == FINDING_REVIEWER
    assert "risk_findings" in data_call.args[2]["data"]


async def test_finding_reviewer_stores_messages_for_low_sev_findings():
    """Gate 2 fires for any findings, not just high/critical."""
    dao = AsyncMock()
    dao.get.return_value = None
    config = {"configurable": {"job_repo": dao}}
    state = {
        "job_id": "job-1",
        "risk_findings": [_make_finding("lodash", 4.0, 0.9, "low", evidence_count=2)],
        "evidence": [],
        "review_iterations": 0,
    }

    with patch("src.main_graph.nodes.finding_reviewer.interrupt", return_value="ok"):
        result = await finding_reviewer(state, config)

    assert result["review_approved"] is True
    calls = dao.push_artifact_message.await_args_list
    assert len(calls) == 2
    assert calls[0].args[2]["role"] == "assistant"
    assert calls[1].args[2]["role"] == "human"


async def test_finding_reviewer_no_messages_when_no_findings():
    """Auto-approves silently when the correlator produced no findings at all."""
    dao = AsyncMock()
    config = {"configurable": {"job_repo": dao}}
    state = {
        "job_id": "job-1",
        "risk_findings": [],
        "evidence": [],
        "review_iterations": 0,
    }

    result = await finding_reviewer(state, config)

    assert result["review_approved"] is True
    dao.push_artifact_message.assert_not_awaited()
    dao.update_artifact_data.assert_not_awaited()


async def test_above_threshold_default_any_passes_all():
    with patch("src.main_graph.nodes.finding_reviewer.settings") as mock_settings:
        mock_settings.reviewer_min_severity = "any"
        assert _above_threshold("low") is True
        assert _above_threshold("medium") is True
        assert _above_threshold("high") is True
        assert _above_threshold("critical") is True


async def test_above_threshold_high_filters_medium_and_below():
    with patch("src.main_graph.nodes.finding_reviewer.settings") as mock_settings:
        mock_settings.reviewer_min_severity = "high"
        assert _above_threshold("critical") is True
        assert _above_threshold("high") is True
        assert _above_threshold("medium") is False
        assert _above_threshold("low") is False
        assert _above_threshold("info") is False


async def test_finding_reviewer_skips_gate_when_findings_below_threshold():
    """When reviewer_min_severity=high, medium findings do not trigger gate 2."""
    dao = AsyncMock()
    config = {"configurable": {"job_repo": dao}}
    state = {
        "job_id": "job-1",
        "risk_findings": [_make_finding("lodash", 5.0, 0.7, "medium", evidence_count=2)],
        "evidence": [],
        "review_iterations": 0,
    }

    with patch("src.main_graph.nodes.finding_reviewer.settings") as mock_settings:
        mock_settings.reviewer_min_severity = "high"
        result = await finding_reviewer(state, config)

    assert result["review_approved"] is True
    dao.push_artifact_message.assert_not_awaited()
    dao.update_artifact_data.assert_not_awaited()


async def test_finding_reviewer_triggers_gate_when_findings_meet_threshold():
    """When reviewer_min_severity=medium, medium findings do trigger gate 2."""
    dao = AsyncMock()
    dao.get.return_value = None
    config = {"configurable": {"job_repo": dao}}
    state = {
        "job_id": "job-1",
        "risk_findings": [_make_finding("lodash", 5.0, 0.7, "medium", evidence_count=2)],
        "evidence": [],
        "review_iterations": 0,
    }

    with (
        patch("src.main_graph.nodes.finding_reviewer.settings") as mock_settings,
        patch("src.main_graph.nodes.finding_reviewer.interrupt", return_value="ok"),
    ):
        mock_settings.reviewer_min_severity = "medium"
        result = await finding_reviewer(state, config)

    assert result["review_approved"] is True
    calls = dao.push_artifact_message.await_args_list
    assert len(calls) == 2
    assert calls[0].args[2]["role"] == "assistant"
    assert calls[1].args[2]["role"] == "human"


async def test_finding_reviewer_skips_push_on_langgraph_rerun():
    """On node re-execution after resume, no new assistant message is pushed."""
    stored_msg = "**High-Severity Findings Require Your Review:**\n..."
    job = Job(
        id="job-1",
        metadata=JobMetadata(repo_url="https://github.com/test/repo", concern="security"),
        artifacts=[{
            "node": FINDING_REVIEWER,
            "status": "running",
            "started_at": None,
            "completed_at": None,
            "messages": [{"role": "assistant", "content": stored_msg, "created_at": "2026-06-28T10:00:00Z"}],
            "data": {"risk_findings": []},
        }],
    )
    dao = AsyncMock()
    dao.get.return_value = job
    config = {"configurable": {"job_repo": dao}}
    state = {
        "job_id": "job-1",
        "risk_findings": [_make_finding("lodash", 8.0, 0.8, "high", evidence_count=3)],
        "evidence": [],
        "review_iterations": 0,
    }

    with patch("src.main_graph.nodes.finding_reviewer.interrupt", return_value="acknowledged"):
        result = await finding_reviewer(state, config)

    assert result["review_approved"] is True

    # Only the human message — no duplicate assistant push
    calls = dao.push_artifact_message.await_args_list
    assert len(calls) == 1
    assert calls[0].args[2]["role"] == "human"
    assert calls[0].args[2]["action"] == "approve"

    dao.update_artifact_data.assert_not_awaited()
