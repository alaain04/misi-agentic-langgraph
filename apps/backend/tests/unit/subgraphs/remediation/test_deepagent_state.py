from src.main_graph.subgraphs.remediation.deepagent.state import _merge_replace


def test_merge_replace_incoming_key_wins():
    current = {"eslint": {"status": "skipped"}}
    incoming = {"eslint": {"status": "fixed"}}
    assert _merge_replace(current, incoming) == {"eslint": {"status": "fixed"}}


def test_merge_replace_keeps_disjoint_keys_from_both():
    current = {"a": {"x": 1}}
    incoming = {"b": {"y": 2}}
    assert _merge_replace(current, incoming) == {"a": {"x": 1}, "b": {"y": 2}}
