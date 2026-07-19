from __future__ import annotations

from langgraph.types import Send

from src.main_graph.subgraphs.report.graph import _dispatch_findings


def test_empty_findings_goes_to_synthesizer():
    assert _dispatch_findings({"findings_to_enrich": []}) == "save_report_result"


def test_missing_findings_key_goes_to_synthesizer():
    assert _dispatch_findings({}) == "save_report_result"


def test_findings_fan_out_via_send():
    finding = {
        "dep_name": "lodash",
        "severity": "high",
        "description": "CVE",
        "evidence": [],
    }
    result = _dispatch_findings({"findings_to_enrich": [finding]})
    assert isinstance(result, list)
    assert len(result) == 1
    assert isinstance(result[0], Send)
    assert result[0].node == "finding_enricher"


def test_multiple_findings_produce_multiple_sends():
    findings = [
        {"dep_name": "lodash", "severity": "high", "description": "d1", "evidence": []},
        {
            "dep_name": "axios",
            "severity": "medium",
            "description": "d2",
            "evidence": [],
        },
    ]
    result = _dispatch_findings({"findings_to_enrich": findings})
    assert isinstance(result, list)
    assert len(result) == 2
    assert all(isinstance(s, Send) and s.node == "finding_enricher" for s in result)
