from langgraph.graph import END

from src.main_graph.constants import ANALYSIS, REMEDIATION, REPORT
from src.main_graph.graph import _after_analysis, _after_prep, build_main_graph


def test_prep_error_goes_to_end():
    assert _after_prep({"discovery_error": "fail"}) == END


def test_prep_success_goes_to_analysis():
    assert _after_prep({"discovery_error": None, "prep_result_id": "p1"}) == ANALYSIS


def test_prep_no_result_id_goes_to_end():
    assert _after_prep({"discovery_error": None}) == END


def test_analysis_success_goes_to_remediation():
    assert _after_analysis({"analysis_result_id": "a1"}) == REMEDIATION


def test_analysis_failure_goes_to_end():
    assert _after_analysis({}) == END


def test_pipeline_includes_remediation_between_analysis_and_report():
    graph = build_main_graph()
    nodes = graph.get_graph().nodes
    assert REMEDIATION in nodes
    # analysis routes to remediation; remediation routes to report
    edges = {(e.source, e.target) for e in graph.get_graph().edges}
    assert (ANALYSIS, REMEDIATION) in edges
    assert any(
        e.source == REMEDIATION and e.target == REPORT
        for e in graph.get_graph().edges
    )
