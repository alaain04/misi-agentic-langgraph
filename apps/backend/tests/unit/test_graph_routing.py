from langgraph.graph import END

from src.main_graph.constants import ANALYSIS, REMEDIATION
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


def test_pipeline_ends_after_remediation():
    # report subgraph is disabled - remediation is the last node before END
    graph = build_main_graph()
    nodes = graph.get_graph().nodes
    assert REMEDIATION in nodes
    assert "report" not in nodes
    edges = {(e.source, e.target) for e in graph.get_graph().edges}
    assert (ANALYSIS, REMEDIATION) in edges
    assert (REMEDIATION, END) in edges
