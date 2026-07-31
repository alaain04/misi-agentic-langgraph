from src.main_graph.subgraphs.remediation.deepagent.grouping import connected_groups


def test_no_edges_every_target_is_its_own_group():
    assert connected_groups(["a", "b"], {}) == [["a"], ["b"]]


def test_chain_of_requires_forms_one_group():
    edges = {"a": ["b"], "b": ["c"]}
    assert connected_groups(["a"], edges) == [["a", "b", "c"]]


def test_independent_pairs_stay_separate():
    edges = {"a": ["b"], "c": ["d"]}
    assert connected_groups(["a", "c"], edges) == [["a", "b"], ["c", "d"]]


def test_companion_only_dependency_is_included():
    # "b" never appears in target_deps, only as something "a" requires
    edges = {"a": ["b"]}
    assert connected_groups(["a"], edges) == [["a", "b"]]


def test_empty_input():
    assert connected_groups([], {}) == []


def test_groups_and_members_are_sorted_for_determinism():
    edges = {"z": ["y"], "x": []}
    assert connected_groups(["z", "x"], edges) == [["x"], ["y", "z"]]
