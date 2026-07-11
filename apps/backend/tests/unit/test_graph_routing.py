from src.main_graph.graph import _after_conductor, _after_hitl, _after_prep
from src.main_graph.constants import CONDUCTOR, HITL_GATE, REPORT_BUILDER, TOOL_RUNNER
from src.models.conductor import ConductorDecision, ToolCall
from langgraph.graph import END


def _decision(**kwargs) -> ConductorDecision:
    defaults = dict(tool_calls=[], findings=[], ask_user=None, checkpoint_message=None, finalize=False, reasoning="r")
    return ConductorDecision(**{**defaults, **kwargs})


def test_after_prep_routes_to_conductor_on_success():
    assert _after_prep({"discovery_error": None}) == CONDUCTOR


def test_after_prep_routes_to_end_on_error():
    assert _after_prep({"discovery_error": "clone failed"}) == END


def test_after_conductor_finalize_autopilot_goes_to_report():
    state = {"conductor_decision": _decision(finalize=True), "autopilot": True}
    assert _after_conductor(state) == REPORT_BUILDER


def test_after_conductor_finalize_non_autopilot_goes_to_hitl():
    state = {"conductor_decision": _decision(finalize=True), "autopilot": False}
    assert _after_conductor(state) == HITL_GATE


def test_after_conductor_ask_user_goes_to_hitl():
    state = {"conductor_decision": _decision(ask_user="what?"), "autopilot": False}
    assert _after_conductor(state) == HITL_GATE


def test_after_conductor_tool_calls_goes_to_runner():
    state = {"conductor_decision": _decision(tool_calls=[ToolCall(tool="npm_audit", args={}, reason="check")])}
    assert _after_conductor(state) == TOOL_RUNNER


def test_after_conductor_empty_decision_goes_to_report():
    state = {"conductor_decision": _decision()}
    assert _after_conductor(state) == REPORT_BUILDER


def test_after_conductor_none_decision_goes_to_report():
    state = {}
    assert _after_conductor(state) == REPORT_BUILDER


def test_after_hitl_finalize_goes_to_report():
    state = {"conductor_decision": _decision(finalize=True)}
    assert _after_hitl(state) == REPORT_BUILDER


def test_after_hitl_non_finalize_goes_to_conductor():
    state = {"conductor_decision": _decision(ask_user="what?")}
    assert _after_hitl(state) == CONDUCTOR
