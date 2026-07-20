from __future__ import annotations

from src.main_graph.subgraphs.discovery.dependency_graph import (
    direct_dependents,
    is_direct,
)

_GRAPH = {
    "direct": {"express": "4.18.0", "webpack": "5.0.0"},
    "packages": {
        "express@4.18.0": {
            "version": "4.18.0",
            "dependencies": ["body-parser@1.20.0"],
        },
        "body-parser@1.20.0": {
            "version": "1.20.0",
            "dependencies": ["qs@6.11.0"],
        },
        "qs@6.11.0": {"version": "6.11.0", "dependencies": []},
        "webpack@5.0.0": {"version": "5.0.0", "dependencies": ["qs@6.11.0"]},
    },
}


def test_is_direct_true_for_declared_dependency():
    assert is_direct(_GRAPH, "express") is True


def test_is_direct_false_for_transitive():
    assert is_direct(_GRAPH, "qs") is False


def test_is_direct_false_when_absent():
    assert is_direct(_GRAPH, "not-installed") is False


def test_direct_dependents_empty_for_direct_dependency():
    assert direct_dependents(_GRAPH, "express") == []


def test_direct_dependents_single_parent():
    assert direct_dependents(_GRAPH, "body-parser") == ["express"]


def test_direct_dependents_shared_transitive_lists_all_sorted():
    # qs is pulled by express (via body-parser) and directly by webpack
    assert direct_dependents(_GRAPH, "qs") == ["express", "webpack"]


def test_direct_dependents_scoped_package_name():
    graph = {
        "direct": {"@nestjs/core": "10.0.0"},
        "packages": {
            "@nestjs/core@10.0.0": {
                "version": "10.0.0",
                "dependencies": ["@scope/leaf@1.0.0"],
            },
            "@scope/leaf@1.0.0": {"version": "1.0.0", "dependencies": []},
        },
    }
    assert direct_dependents(graph, "@scope/leaf") == ["@nestjs/core"]


def test_direct_dependents_empty_when_no_transitive_data():
    # package.json fallback: direct names but no packages graph
    graph = {"direct": {"lodash": "^4.17.21"}, "packages": {}}
    assert direct_dependents(graph, "some-transitive") == []


def test_direct_dependents_empty_graph():
    assert direct_dependents({}, "anything") == []
