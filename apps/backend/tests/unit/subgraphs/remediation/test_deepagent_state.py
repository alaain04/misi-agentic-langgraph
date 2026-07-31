from src.main_graph.subgraphs.remediation.deepagent.state import (
    RemediationDeepAgentState,
    _keep_first_dict,
    _keep_first_str,
    _merge_replace,
)


def test_keep_first_str_keeps_existing_truthy_value():
    assert _keep_first_str("job-1", "job-2") == "job-1"


def test_keep_first_str_takes_incoming_when_current_empty():
    assert _keep_first_str("", "job-2") == "job-2"


def test_keep_first_dict_keeps_existing_truthy_value():
    assert _keep_first_dict({"a": 1}, {"b": 2}) == {"a": 1}


def test_keep_first_dict_takes_incoming_when_current_empty():
    assert _keep_first_dict({}, {"b": 2}) == {"b": 2}


def test_merge_replace_incoming_key_wins():
    current = {"eslint": {"status": "skipped"}}
    incoming = {"eslint": {"status": "fixed"}}
    assert _merge_replace(current, incoming) == {"eslint": {"status": "fixed"}}


def test_merge_replace_keeps_disjoint_keys_from_both():
    current = {"a": {"x": 1}}
    incoming = {"b": {"y": 2}}
    assert _merge_replace(current, incoming) == {"a": {"x": 1}, "b": {"y": 2}}


def test_state_schema_declares_expected_fields():
    hints = RemediationDeepAgentState.__annotations__
    expected_fields = (
        "job_id",
        "prep_result_id",
        "evidence",
        "targets",
        "remediations",
        "requires_edges",
    )
    for field in expected_fields:
        assert field in hints
