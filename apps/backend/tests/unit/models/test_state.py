from typing import get_type_hints

from src.main_graph.state import MainState


def test_state_has_evidence_field():
    hints = get_type_hints(MainState, include_extras=True)
    assert "evidence" in hints


def test_state_has_investigation_plan_field():
    hints = get_type_hints(MainState, include_extras=True)
    assert "investigation_plan" in hints


def test_state_has_risk_findings_field():
    hints = get_type_hints(MainState, include_extras=True)
    assert "risk_findings" in hints
