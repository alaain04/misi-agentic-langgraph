from src.models.results import (
    PrepResult,
    AgentDispatch,
    AnalysisConductorDecision,
    EvidenceBundle,
    AgentCallRecord,
    AnalysisResult,
    ReportFinding,
    ReportResult,
    DomainAgentDecision,
)
from src.models.conductor import FindingNote, EvidenceRef


def _finding() -> FindingNote:
    return FindingNote(
        dep_name="express", severity="high", description="CVE", evidence=[]
    )


def test_prep_result_auto_id_and_timestamp():
    r = PrepResult(
        job_id="j1",
        repo_path="/tmp/r",
        project_metadata={},
        manifest_files=["package.json"],
        detected_package_manager="npm",
        dependency_graph={"direct": {}},
        discovery_summary="summary",
        vector_store_id="vs1",
    )
    assert r.id
    assert r.created_at


def test_evidence_bundle_round_trip():
    b = EvidenceBundle(
        domain="vulnerabilities",
        hypothesis="h",
        findings=[_finding()],
        summary="s",
        confidence=0.9,
    )
    data = b.model_dump()
    b2 = EvidenceBundle(**data)
    assert b2.id == b.id
    assert b2.findings[0].dep_name == "express"


def test_analysis_conductor_decision_finalize():
    d = AnalysisConductorDecision(dispatches=[], finalize=True, reasoning="done")
    assert d.finalize
    assert d.dispatches == []


def test_agent_dispatch():
    d = AgentDispatch(
        domain="vulnerabilities",
        hypothesis="check CVEs",
        packages_to_focus=["express"],
        agent_type="vulnerability_agent",
    )
    assert d.agent_type == "vulnerability_agent"


def test_domain_agent_decision():
    d = DomainAgentDecision(
        tool_calls=[],
        findings=[_finding()],
        summary="found 1 CVE",
        confidence=0.85,
        finalize=True,
        reasoning="r",
    )
    assert d.confidence == 0.85


def test_evidence_bundle_verification_note_defaults_none():
    b = EvidenceBundle(
        domain="d", hypothesis="h", findings=[], summary="s", confidence=0.5
    )
    assert b.verification_note is None


def test_evidence_bundle_accepts_verification_note():
    b = EvidenceBundle(
        domain="d",
        hypothesis="h",
        findings=[],
        summary="s",
        confidence=0.3,
        verification_note="finding 1 lacks evidence",
    )
    assert b.verification_note == "finding 1 lacks evidence"


def test_agent_call_record_round_trip():
    record = AgentCallRecord(
        conductor_iteration=1,
        agent_type="vulnerability_agent",
        domain="vulnerability",
        tools_used=["npm_audit"],
        react_iterations=1,
        started_at="2026-07-12T00:00:00+00:00",
        finished_at="2026-07-12T00:00:05+00:00",
        bundle_id="bundle-1",
    )
    data = record.model_dump()
    record2 = AgentCallRecord(**data)
    assert record2.tools_used == ["npm_audit"]
    assert record2.conductor_iteration == 1


def test_report_result_round_trip():
    r = ReportResult(
        job_id="j1",
        concern="outdated deps",
        executive_summary="2 high risks found",
        overall_risk_level="high",
        findings=[
            ReportFinding(
                dep_name="express",
                severity="high",
                description="CVE",
                recommendation="upgrade",
                alternatives=["fastify"],
                affected_files=["src/server.ts:3"],
            )
        ],
        recommendations=["Upgrade express"],
    )
    assert r.id
    assert r.findings[0].alternatives == ["fastify"]
