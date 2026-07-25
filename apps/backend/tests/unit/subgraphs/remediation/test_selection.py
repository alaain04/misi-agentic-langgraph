from src.main_graph.subgraphs.remediation.selection import select_remediation_targets
from src.models.conductor import FindingNote

GRAPH = {
    "direct": {"mkdirp": "0.5.1", "lodash": "4.17.11", "optimist": "0.6.1"},
    "packages": {
        "mkdirp@0.5.1": {"dependencies": ["minimist@0.0.8"]},
        "optimist@0.6.1": {"dependencies": ["minimist@0.0.8"]},
        "minimist@0.0.8": {"dependencies": []},
        "lodash@4.17.11": {"dependencies": []},
    },
}


def _f(dep, sev="high"):
    return FindingNote(
        dep_name=dep,
        severity=sev,
        description=f"{dep} issue",
        evidence=[],
    )


def test_direct_finding_maps_to_itself():
    targets = select_remediation_targets([_f("lodash")], GRAPH, "high")
    assert [t.target_dep for t in targets] == ["lodash"]
    assert targets[0].addresses == ["lodash"]
    assert targets[0].current_range == "4.17.11"


def test_transitive_anchors_to_direct_parent():
    targets = select_remediation_targets([_f("minimist")], GRAPH, "high")
    # minimist is pulled by mkdirp AND optimist -> two targets, both addressing minimist
    assert sorted(t.target_dep for t in targets) == ["mkdirp", "optimist"]
    assert all(t.addresses == ["minimist"] for t in targets)


def test_two_transitives_under_same_direct_unify():
    graph = {
        "direct": {"parent": "1.0.0"},
        "packages": {
            "parent@1.0.0": {"dependencies": ["a@1", "b@1"]},
            "a@1": {"dependencies": []},
            "b@1": {"dependencies": []},
        },
    }
    targets = select_remediation_targets([_f("a"), _f("b")], graph, "high")
    assert len(targets) == 1
    assert targets[0].target_dep == "parent"
    assert targets[0].addresses == ["a", "b"]


def test_severity_filter_drops_below_floor():
    targets = select_remediation_targets([_f("lodash", "low")], GRAPH, "high")
    assert targets == []


def test_unanchorable_transitive_is_dropped():
    graph = {"direct": {"x": "1.0.0"}, "packages": {}}  # no edges to trace
    targets = select_remediation_targets([_f("ghost")], graph, "high")
    assert targets == []


def test_targets_sorted_by_dep():
    targets = select_remediation_targets([_f("optimist"), _f("lodash")], GRAPH, "high")
    assert [t.target_dep for t in targets] == ["lodash", "optimist"]
