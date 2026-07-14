from src.main_graph.graph import _after_prep, _after_analysis
from src.main_graph.constants import ANALYSIS, REPORT
from langgraph.graph import END


def test_prep_error_goes_to_end():
    assert _after_prep({"discovery_error": "fail"}) == END


def test_prep_success_goes_to_analysis():
    assert _after_prep({"discovery_error": None, "prep_result_id": "p1"}) == ANALYSIS


def test_prep_no_result_id_goes_to_end():
    assert _after_prep({"discovery_error": None}) == END


def test_analysis_success_goes_to_report():
    assert _after_analysis({"analysis_result_id": "a1"}) == REPORT


def test_analysis_failure_goes_to_end():
    assert _after_analysis({}) == END
