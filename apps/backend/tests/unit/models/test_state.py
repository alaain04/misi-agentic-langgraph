from typing import get_type_hints

from langgraph.graph.message import add_messages

from src.main_graph.state import MainState


def test_messages_has_add_messages_reducer():
    hints = get_type_hints(MainState, include_extras=True)
    messages_hint = hints["messages"]
    metadata = getattr(messages_hint, "__metadata__", ())
    assert add_messages in metadata


def test_required_input_fields_present():
    hints = get_type_hints(MainState)
    for field in ("repo_url", "concern", "job_id", "autopilot"):
        assert field in hints


def test_result_id_fields_present():
    hints = get_type_hints(MainState)
    for field in ("prep_result_id", "analysis_result_id", "report_result_id"):
        assert field in hints
